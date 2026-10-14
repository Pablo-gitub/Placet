"""
Injection delivery with acknowledgement: claim before sending, ack from the
agent (MCP tool or CLI hook), bounded retries, DELIVERY_FAILED, STALLED and
implicit replies at the end of the agent's turn.
"""
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from backend.app.config import AgentSpec, PlacetConfig
from backend.app.database import Database
from backend.app.main import create_app
from backend.app.models import (
    ACK_TIMEOUT_SECONDS,
    MAX_DELIVERY_ATTEMPTS,
    RequestCreate,
    RequestStatus,
)
from bridge.bridge_daemon import BridgeDaemon
from bridge.tmux_controller import TmuxController
from tests.helpers import agent_client, human_client


def _config(**gemini):
    return PlacetConfig(agents={
        "claude": AgentSpec(tmux_session="agent-claude", token="tok-claude",
                            allowed_targets=["gemini", "human"]),
        "gemini": AgentSpec(tmux_session="agent-gemini", token="tok-gemini",
                            allowed_targets=["claude", "human"], **gemini),
        "codex": AgentSpec(tmux_session="agent-codex", token="tok-codex", allowed_targets=["human"]),
    })


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
    mock.seconds_since_input.return_value = None
    return mock


def _approved(db, prompt="Analizza"):
    req = db.create_request(RequestCreate(sender_id="claude", recipient_id="gemini", prompt=prompt))
    db.update_request_decision(req.id, RequestStatus.APPROVED, actor="human:p")
    return req


def _backdate(db, req_id, column, seconds):
    past = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
    with db._get_connection() as conn:
        conn.execute(f"UPDATE requests SET {column} = ? WHERE id = ?", (past, req_id))


def _events(db, req_id):
    return [e.event for e in db.list_events(req_id)]


# --- Bridge side ---------------------------------------------------------------

def test_claim_happens_before_sending(db, tmux):
    req = _approved(db)
    seen = []
    tmux.send_keys.side_effect = lambda *a, **k: seen.append(db.get_request(req.id).status) or True
    BridgeDaemon(db=db, config=_config(), tmux=tmux).run_loop_step()
    assert seen == [RequestStatus.DELIVERING]
    assert db.get_request(req.id).delivery_attempts == 1


def test_failed_send_releases_without_counting(db, tmux):
    req = _approved(db)
    tmux.send_keys.return_value = False
    BridgeDaemon(db=db, config=_config(), tmux=tmux).run_loop_step()
    after = db.get_request(req.id)
    assert after.status == RequestStatus.APPROVED
    assert after.delivery_attempts == 0
    assert "injection_released" in _events(db, req.id)


def test_ack_mode_none_confirms_immediately(db, tmux):
    req = _approved(db)
    BridgeDaemon(db=db, config=_config(ack_mode="none"), tmux=tmux).run_loop_step()
    assert db.get_request(req.id).status == RequestStatus.EXECUTING
    assert "placet_ack" not in tmux.send_keys.call_args.args[1]


def test_envelope_asks_for_ack_only_in_mcp_mode(db, tmux):
    _approved(db)
    BridgeDaemon(db=db, config=_config(ack_mode="hook"), tmux=tmux).run_loop_step()
    assert "placet_ack" not in tmux.send_keys.call_args.args[1]


def test_one_delivery_in_flight_per_recipient(db, tmux):
    first, second = _approved(db, "uno"), _approved(db, "due")
    daemon = BridgeDaemon(db=db, config=_config(), tmux=tmux)
    daemon.run_loop_step()
    daemon.run_loop_step()  # first still waiting for its ack
    assert db.get_request(first.id).status == RequestStatus.DELIVERING
    assert db.get_request(second.id).status == RequestStatus.APPROVED
    assert tmux.send_keys.call_count == 1

    db.confirm_injection(first.id, actor="agent:gemini", via="mcp")
    daemon.run_loop_step()
    assert db.get_request(second.id).status == RequestStatus.DELIVERING


def test_recent_user_input_postpones_injection(db, tmux):
    req = _approved(db)
    tmux.seconds_since_input.return_value = 3.0
    BridgeDaemon(db=db, config=_config(), tmux=tmux).run_loop_step()
    assert db.get_request(req.id).status == RequestStatus.APPROVED
    tmux.send_keys.assert_not_called()

    tmux.seconds_since_input.return_value = 60.0
    BridgeDaemon(db=db, config=_config(), tmux=tmux).run_loop_step()
    assert db.get_request(req.id).status == RequestStatus.DELIVERING


def test_agent_declared_busy_is_skipped_even_if_screen_looks_idle(db, tmux):
    req = _approved(db)
    db.set_agent_state("gemini", "busy")
    BridgeDaemon(db=db, config=_config(), tmux=tmux).run_loop_step()
    assert db.get_request(req.id).status == RequestStatus.APPROVED

    db.set_agent_state("gemini", "idle")
    BridgeDaemon(db=db, config=_config(), tmux=tmux).run_loop_step()
    assert db.get_request(req.id).status == RequestStatus.DELIVERING


def test_declared_idle_still_requires_idle_screen(db, tmux):
    req = _approved(db)
    db.set_agent_state("gemini", "idle")
    tmux.is_session_idle.return_value = False  # e.g. a menu is open
    BridgeDaemon(db=db, config=_config(), tmux=tmux).run_loop_step()
    assert db.get_request(req.id).status == RequestStatus.APPROVED


# --- Retries and failure ---------------------------------------------------------

def test_unacked_injection_is_retried_then_fails(db):
    req = _approved(db)
    for attempt in range(1, MAX_DELIVERY_ATTEMPTS + 1):
        assert db.claim_injection(req.id) is not None
        _backdate(db, req.id, "delivering_since", ACK_TIMEOUT_SECONDS + 1)
        requeued, failed = db.requeue_unacked(ACK_TIMEOUT_SECONDS, MAX_DELIVERY_ATTEMPTS)
        if attempt < MAX_DELIVERY_ATTEMPTS:
            assert requeued == [req.id] and failed == []
            assert db.get_request(req.id).status == RequestStatus.APPROVED
        else:
            assert failed == [req.id]
    final = db.get_request(req.id)
    assert final.status == RequestStatus.DELIVERY_FAILED
    assert _events(db, req.id).count("injection_requeued") == MAX_DELIVERY_ATTEMPTS - 1
    assert "injection_failed" in _events(db, req.id)


def test_fresh_injection_is_not_requeued(db):
    req = _approved(db)
    db.claim_injection(req.id)
    assert db.requeue_unacked(ACK_TIMEOUT_SECONDS, MAX_DELIVERY_ATTEMPTS) == ([], [])


@pytest.mark.asyncio
async def test_delivery_failure_alerts_the_human(db, monkeypatch):
    alerts = []

    async def fake_alert(request, cfg, problem):
        alerts.append((request.id, problem))
        return True

    monkeypatch.setattr("backend.app.main.send_ntfy_alert", fake_alert)
    app = create_app(db=db, config=_config())
    req = _approved(db)
    for _ in range(MAX_DELIVERY_ATTEMPTS):
        db.claim_injection(req.id)
        _backdate(db, req.id, "delivering_since", ACK_TIMEOUT_SECONDS + 1)
        async with human_client(app) as human:
            await human.get("/api/requests")  # runs the sweep
    import asyncio
    await asyncio.sleep(0)
    assert db.get_request(req.id).status == RequestStatus.DELIVERY_FAILED
    assert alerts == [(req.id, "consegna fallita")]


def test_stalled_after_execution_timeout(db):
    req = _approved(db)
    db.claim_injection(req.id)
    db.confirm_injection(req.id, actor="agent:gemini", via="mcp")
    assert db.stall_executing(lambda agent: 60) == []
    _backdate(db, req.id, "executing_since", 61)
    assert db.stall_executing(lambda agent: 60) == [req.id]
    assert db.get_request(req.id).status == RequestStatus.STALLED


def test_outcome_envelope_explains_failures(db, tmux):
    req = _approved(db)
    db.claim_injection(req.id)
    db.confirm_injection(req.id, actor="agent:gemini", via="mcp")
    _backdate(db, req.id, "executing_since", 3600)
    db.stall_executing(lambda agent: 60)
    _backdate(db, req.id, "updated_at", 30)
    BridgeDaemon(db=db, config=_config(), tmux=tmux).run_loop_step()
    text = tmux.send_keys.call_args.args[1]
    assert "STALLED" in text and "non ha risposto" in text


# --- Agent API: ack and turn state ----------------------------------------------

@pytest.mark.asyncio
async def test_ack_endpoint(db):
    app = create_app(db=db, config=_config())
    req = _approved(db)
    db.claim_injection(req.id)
    async with agent_client(app, "tok-claude") as sender:
        assert (await sender.post(f"/api/requests/{req.id}/ack")).status_code == 403
    async with agent_client(app, "tok-codex") as outsider:
        assert (await outsider.post(f"/api/requests/{req.id}/ack")).status_code == 404
    async with agent_client(app, "tok-gemini") as gemini:
        first = await gemini.post(f"/api/requests/{req.id}/ack")
        again = await gemini.post(f"/api/requests/{req.id}/ack")
    assert first.status_code == 200 and first.json()["status"] == "EXECUTING"
    assert again.status_code == 200  # idempotent
    assert _events(db, req.id).count("acked") == 1


@pytest.mark.asyncio
async def test_ack_before_injection_is_conflict(db):
    app = create_app(db=db, config=_config())
    req = _approved(db)
    async with agent_client(app, "tok-gemini") as gemini:
        assert (await gemini.post(f"/api/requests/{req.id}/ack")).status_code == 409


@pytest.mark.asyncio
async def test_reply_while_delivering_counts_as_received(db):
    app = create_app(db=db, config=_config())
    req = _approved(db)
    db.claim_injection(req.id)
    async with agent_client(app, "tok-gemini") as gemini:
        resp = await gemini.post(f"/api/requests/{req.id}/reply", json={"content": "fatto"})
    assert resp.json()["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_hook_turn_start_acks_and_turn_end_records_implicit_reply(db):
    app = create_app(db=db, config=_config())
    req = _approved(db)
    db.claim_injection(req.id)
    async with agent_client(app, "tok-gemini") as gemini:
        busy = await gemini.post("/api/agents/me/state", json={"state": "busy", "request_id": req.id})
        assert busy.json()["turn_request_id"] == req.id
        assert db.get_request(req.id).status == RequestStatus.EXECUTING
        idle = await gemini.post("/api/agents/me/state",
                                 json={"state": "idle", "last_message": "Ecco l'analisi"})
    assert idle.json()["implicit_reply_for"] == req.id
    done = db.get_request(req.id)
    assert done.status == RequestStatus.COMPLETED
    assert done.response.implicit is True
    assert done.response.content == "Ecco l'analisi"
    assert ("agent:gemini:hook", "replied") in [(e.actor, e.event) for e in db.list_events(req.id)]


@pytest.mark.asyncio
async def test_explicit_reply_wins_over_implicit(db):
    app = create_app(db=db, config=_config())
    req = _approved(db)
    db.claim_injection(req.id)
    async with agent_client(app, "tok-gemini") as gemini:
        await gemini.post("/api/agents/me/state", json={"state": "busy", "request_id": req.id})
        await gemini.post(f"/api/requests/{req.id}/reply", json={"content": "risposta vera"})
        idle = await gemini.post("/api/agents/me/state", json={"state": "idle", "last_message": "altro"})
    assert "implicit_reply_for" not in idle.json()
    assert db.get_request(req.id).response.content == "risposta vera"
    assert db.get_request(req.id).response.implicit is False


@pytest.mark.asyncio
async def test_unrelated_turn_does_not_produce_implicit_reply(db):
    """A turn the user started (no Placet request) must not answer a request."""
    app = create_app(db=db, config=_config())
    req = _approved(db)
    db.claim_injection(req.id)
    db.confirm_injection(req.id, actor="agent:gemini", via="mcp")
    async with agent_client(app, "tok-gemini") as gemini:
        await gemini.post("/api/agents/me/state", json={"state": "busy"})
        await gemini.post("/api/agents/me/state", json={"state": "idle", "last_message": "ciao Paolo"})
    assert db.get_request(req.id).status == RequestStatus.EXECUTING


@pytest.mark.asyncio
async def test_state_endpoint_is_agent_only(db):
    app = create_app(db=db, config=_config())
    async with human_client(app) as human:
        assert (await human.post("/api/agents/me/state", json={"state": "idle"})).status_code == 403


def test_busy_state_expires(db):
    db.set_agent_state("gemini", "busy")
    assert db.busy_agents(60) == ["gemini"]
    with db._get_connection() as conn:
        conn.execute("UPDATE agent_states SET updated_at = ?",
                     ((datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat(),))
    assert db.busy_agents(60) == []


def test_seconds_since_input_parses_tmux(monkeypatch):
    controller = TmuxController()
    now = int(time.time())
    monkeypatch.setattr("subprocess.run", lambda *a, **k: MagicMock(stdout=f"{now - 100}\n{now - 5}\n"))
    assert 4 <= controller.seconds_since_input("s") <= 7
    monkeypatch.setattr("subprocess.run", lambda *a, **k: MagicMock(stdout=""))
    assert controller.seconds_since_input("s") is None
