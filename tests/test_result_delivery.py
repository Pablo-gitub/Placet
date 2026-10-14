"""
RF-22: outcomes reach the sender's session when its long-poll is over, and
are not injected twice when the sender already received them.
"""
import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from backend.app.config import AgentSpec, PlacetConfig
from backend.app.database import Database
from backend.app.main import create_app
from backend.app.models import RequestCreate, RequestStatus
from bridge.bridge_daemon import BridgeDaemon
from bridge.tmux_controller import TmuxController
from tests.helpers import agent_client, human_client

CONFIG = PlacetConfig(
    agents={
        "claude": AgentSpec(tmux_session="agent-claude", token="tok-claude",
                            allowed_targets=["gemini", "human"]),
        "gemini": AgentSpec(tmux_session="agent-gemini", token="tok-gemini",
                            allowed_targets=["claude", "human"]),
    }
)


@pytest.fixture
def db():
    database = Database(os.path.join(tempfile.mkdtemp(), "placet.db"))
    database.init_db()
    return database


@pytest.fixture
def tmux():
    mock = MagicMock(spec=TmuxController)
    mock.session_exists.return_value = True
    mock.is_session_idle.return_value = True
    mock.send_keys.return_value = True
    return mock


def _age(db, req_id, seconds):
    """Pretend the last status change happened `seconds` ago."""
    past = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
    with db._get_connection() as conn:
        conn.execute("UPDATE requests SET updated_at = ? WHERE id = ?", (past, req_id))


def _injected(tmux):
    return [(c.args[0], c.args[1]) for c in tmux.send_keys.call_args_list]


def test_human_decision_is_injected_into_sender_session(db, tmux):
    req = db.create_request(RequestCreate(
        sender_id="claude", recipient_id="human", prompt="Pubblico?", options=["Sì", "No"]
    ))
    db.update_request_decision(req.id, RequestStatus.APPROVED, selected_options=["Sì"],
                               custom_text="ma senza emoji")
    _age(db, req.id, 30)

    daemon = BridgeDaemon(db=db, config=CONFIG, tmux=tmux)
    assert daemon.run_loop_step() == 1

    [(session, text)] = _injected(tmux)
    assert session == "agent-claude"
    assert text.startswith(f"[Placet] Decisione dell'utente sulla richiesta #{req.id}: APPROVED")
    assert "Sì" in text and "ma senza emoji" in text
    assert db.get_request(req.id).delivered_at is not None

    # Never delivered twice.
    tmux.send_keys.reset_mock()
    assert daemon.run_loop_step() == 0
    tmux.send_keys.assert_not_called()


def test_agent_reply_is_injected_into_sender_session(db, tmux):
    req = db.create_request(RequestCreate(sender_id="claude", recipient_id="gemini", prompt="x"))
    db.update_request_decision(req.id, RequestStatus.APPROVED)
    db.update_request_decision(req.id, RequestStatus.EXECUTING)
    db.record_reply(req.id, "gemini", "Complessità O(n log n)")
    _age(db, req.id, 30)

    BridgeDaemon(db=db, config=CONFIG, tmux=tmux).run_loop_step()

    [(session, text)] = _injected(tmux)
    assert session == "agent-claude"
    assert "COMPLETED" in text
    assert "Risposta di gemini: Complessità O(n log n)" in text


@pytest.mark.parametrize("status", [RequestStatus.REJECTED, RequestStatus.EXPIRED])
def test_negative_outcomes_are_delivered_too(db, tmux, status):
    req = db.create_request(RequestCreate(sender_id="claude", recipient_id="gemini", prompt="x"))
    if status == RequestStatus.REJECTED:
        db.update_request_decision(req.id, RequestStatus.REJECTED)
    else:
        with db._get_connection() as conn:
            conn.execute("UPDATE requests SET expires_at = ?", (datetime.now(timezone.utc).isoformat(),))
        db.expire_stale_requests()
    _age(db, req.id, 30)

    BridgeDaemon(db=db, config=CONFIG, tmux=tmux).run_loop_step()
    [(_, text)] = _injected(tmux)
    assert status.value in text


def test_agent_request_approved_but_not_completed_is_not_delivered(db, tmux):
    req = db.create_request(RequestCreate(sender_id="claude", recipient_id="gemini", prompt="x"))
    db.update_request_decision(req.id, RequestStatus.APPROVED)
    _age(db, req.id, 30)
    tmux.session_exists.side_effect = lambda s: s == "agent-claude"  # gemini offline

    assert BridgeDaemon(db=db, config=CONFIG, tmux=tmux).run_loop_step() == 0
    tmux.send_keys.assert_not_called()


def test_grace_period_and_max_age(db, tmux):
    fresh = db.create_request(RequestCreate(sender_id="claude", recipient_id="human", prompt="a"))
    db.update_request_decision(fresh.id, RequestStatus.APPROVED)
    stale = db.create_request(RequestCreate(sender_id="claude", recipient_id="human", prompt="b"))
    db.update_request_decision(stale.id, RequestStatus.APPROVED)
    _age(db, stale.id, 7 * 3600)

    assert BridgeDaemon(db=db, config=CONFIG, tmux=tmux).run_loop_step() == 0
    tmux.send_keys.assert_not_called()


def test_busy_or_missing_sender_session_retries_later(db, tmux):
    req = db.create_request(RequestCreate(sender_id="claude", recipient_id="human", prompt="x"))
    db.update_request_decision(req.id, RequestStatus.APPROVED)
    _age(db, req.id, 30)
    daemon = BridgeDaemon(db=db, config=CONFIG, tmux=tmux)

    tmux.is_session_idle.return_value = False
    assert daemon.run_loop_step() == 0
    assert db.get_request(req.id).delivered_at is None

    tmux.is_session_idle.return_value = True
    assert daemon.run_loop_step() == 1


def test_failed_injection_is_retried(db, tmux):
    req = db.create_request(RequestCreate(sender_id="claude", recipient_id="human", prompt="x"))
    db.update_request_decision(req.id, RequestStatus.APPROVED)
    _age(db, req.id, 30)
    daemon = BridgeDaemon(db=db, config=CONFIG, tmux=tmux)

    tmux.send_keys.return_value = False
    assert daemon.run_loop_step() == 0
    assert db.get_request(req.id).delivered_at is None

    tmux.send_keys.return_value = True
    assert daemon.run_loop_step() == 1


def test_one_injection_per_session_per_tick(db, tmux):
    # An outcome for claude and a new task for claude: only one per tick.
    done = db.create_request(RequestCreate(sender_id="claude", recipient_id="human", prompt="x"))
    db.update_request_decision(done.id, RequestStatus.APPROVED)
    _age(db, done.id, 30)
    task = db.create_request(RequestCreate(sender_id="gemini", recipient_id="claude", prompt="y"))
    db.update_request_decision(task.id, RequestStatus.APPROVED)

    daemon = BridgeDaemon(db=db, config=CONFIG, tmux=tmux)
    assert daemon.run_loop_step() == 1
    assert "[Placet] Decisione" in _injected(tmux)[0][1]
    assert daemon.run_loop_step() == 1
    assert "[Placet Request" in _injected(tmux)[1][1]


@pytest.mark.asyncio
async def test_long_poll_delivery_prevents_injection(db, tmux):
    app = create_app(db=db, config=CONFIG)
    req = db.create_request(RequestCreate(sender_id="claude", recipient_id="human", prompt="x"))
    async with human_client(app) as human:
        await human.post(f"/api/requests/{req.id}/decision", json={"status": "APPROVED"})
    async with agent_client(app, "tok-claude") as claude:
        resp = await claude.get(f"/api/requests/{req.id}")
        assert resp.json()["delivered_at"] is not None
    _age(db, req.id, 30)

    assert BridgeDaemon(db=db, config=CONFIG, tmux=tmux).run_loop_step() == 0
    tmux.send_keys.assert_not_called()


@pytest.mark.asyncio
async def test_reads_by_others_do_not_count_as_delivery(db):
    app = create_app(db=db, config=CONFIG)
    req = db.create_request(RequestCreate(sender_id="claude", recipient_id="gemini", prompt="x"))
    async with human_client(app) as human:
        await human.post(f"/api/requests/{req.id}/decision", json={"status": "REJECTED"})
        await human.get(f"/api/requests/{req.id}")
    async with agent_client(app, "tok-gemini") as gemini:
        await gemini.get(f"/api/requests/{req.id}")
    assert db.get_request(req.id).delivered_at is None


@pytest.mark.asyncio
async def test_cancel_by_sender_is_already_delivered(db):
    app = create_app(db=db, config=CONFIG)
    by_sender = db.create_request(RequestCreate(sender_id="claude", recipient_id="gemini", prompt="x"))
    by_human = db.create_request(RequestCreate(sender_id="claude", recipient_id="gemini", prompt="y"))
    async with agent_client(app, "tok-claude") as claude:
        await claude.delete(f"/api/requests/{by_sender.id}")
    async with human_client(app) as human:
        await human.delete(f"/api/requests/{by_human.id}")
    assert db.get_request(by_sender.id).delivered_at is not None
    assert db.get_request(by_human.id).delivered_at is None
