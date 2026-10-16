"""
Delivery, execution and reply of a request (RF-29), one request at a time
per session (RF-29b).
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from backend.app.models import ACK_TIMEOUT_SECONDS, RequestStatus
from bridge import bridge_daemon
from bridge.bridge_daemon import BridgeDaemon
from bridge.channels import ChannelUncertain
from bridge.stores import HttpStore
from tests.helpers import agent_client, human_client
from tests.test_session_messages import setup, start, write  # noqa: F401


async def event(app, agent, session_id, **body):
    async with agent_client(app, f"tok-{agent}") as client:
        resp = await client.post(f"/api/sessions/{session_id}/events", json=body)
        assert resp.status_code == 200
        return resp.json()


async def take(app, session_id="c1"):
    async with agent_client(app, "tok-claude") as claude:
        resp = await claude.get(f"/api/sessions/{session_id}/inbox")
        return resp.json() if resp.status_code == 200 else None


def aspects(db, req_id):
    req = db.get_request(req_id)
    return req.delivery, req.execution, req.reply


def age_delivery(db, req_id, seconds=ACK_TIMEOUT_SECONDS + 5):
    then = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
    with db._get_connection() as conn:
        conn.execute("UPDATE requests SET delivering_since = ? WHERE id = ?", (then, req_id))


@pytest.mark.asyncio
async def test_a_turn_records_all_three_aspects(setup):  # noqa: F811
    app, db = setup
    await start(app, "claude", "c1", "claude-code")
    req_id = (await write(app, "c1", "fai il punto")).json()["id"]
    assert aspects(db, req_id) == ("waiting", "unobserved", "none")
    await take(app)
    req = db.get_request(req_id)
    assert (req.delivery, req.delivery_channel) == ("in_progress", "waiter") and req.attempt_id
    await event(app, "claude", "c1", event="prompt", request_id=req_id)
    assert aspects(db, req_id) == ("accepted", "started", "none")
    await event(app, "claude", "c1", event="stop", last_message="Ecco il punto.")
    assert aspects(db, req_id) == ("accepted", "completed", "implicit")
    assert db.get_request(req_id).delivery_at is not None


@pytest.mark.asyncio
async def test_an_explicit_reply_does_not_end_the_turn(setup):  # noqa: F811
    app, db = setup
    await start(app, "claude", "c1", "claude-code")
    req_id = (await write(app, "c1")).json()["id"]
    await take(app)
    await event(app, "claude", "c1", event="prompt", request_id=req_id)
    async with agent_client(app, "tok-claude") as claude:
        await claude.post(f"/api/requests/{req_id}/reply", json={"content": "fatto"})
    assert aspects(db, req_id) == ("accepted", "started", "explicit")
    await event(app, "claude", "c1", event="stop", last_message="altro")
    assert aspects(db, req_id) == ("accepted", "completed", "explicit")


@pytest.mark.asyncio
async def test_another_message_in_the_turn_makes_the_reply_ambiguous(setup):  # noqa: F811
    app, db = setup
    await start(app, "claude", "c1", "claude-code")
    req_id = (await write(app, "c1")).json()["id"]
    await take(app)
    await event(app, "claude", "c1", event="prompt", request_id=req_id)
    result = await event(app, "claude", "c1", event="prompt")  # typed in the app
    assert result["turn_request_id"] == req_id
    await event(app, "claude", "c1", event="stop", last_message="risposta a entrambi")
    assert aspects(db, req_id)[2] == "ambiguous"
    assert db.get_request(req_id).status == RequestStatus.COMPLETED


@pytest.mark.asyncio
async def test_a_session_closed_mid_turn_interrupts_the_execution(setup):  # noqa: F811
    app, db = setup
    await start(app, "claude", "c1", "claude-code")
    req_id = (await write(app, "c1")).json()["id"]
    await take(app)
    await event(app, "claude", "c1", event="prompt", request_id=req_id)
    await event(app, "claude", "c1", event="end")
    assert aspects(db, req_id)[1] == "interrupted"


@pytest.mark.asyncio
async def test_one_request_at_a_time_per_session(setup):  # noqa: F811
    app, db = setup
    await start(app, "claude", "c1", "claude-code")
    first = (await write(app, "c1", "primo")).json()["id"]
    second = (await write(app, "c1", "secondo")).json()["id"]
    assert (await take(app))["request_id"] == first
    assert await take(app) is None  # the first is being delivered
    await event(app, "claude", "c1", event="prompt", request_id=first)
    await event(app, "claude", "c1", event="stop", last_message="ok")
    assert (await take(app))["request_id"] == second


@pytest.mark.asyncio
async def test_an_unconfirmed_delivery_becomes_uncertain_and_waits_for_the_user(setup):  # noqa: F811
    app, db = setup
    await start(app, "claude", "c1", "claude-code")
    first = (await write(app, "c1", "primo")).json()["id"]
    second = (await write(app, "c1", "secondo")).json()["id"]
    await take(app)
    age_delivery(db, first)
    assert await take(app) is None  # the sweep runs on the waiter's poll
    req = db.get_request(first)
    assert req.status == RequestStatus.DELIVERING and req.delivery == "uncertain"
    assert req.delivery_attempts == 1  # never re-sent by itself
    assert await take(app) is None  # it blocks the next one

    attempt = req.attempt_id
    async with human_client(app) as human:
        retried = (await human.post(f"/api/requests/{first}/delivery/retry")).json()
        assert retried["status"] == "APPROVED" and retried["delivery"] == "waiting"
        assert (await human.post(f"/api/requests/{first}/delivery/retry")).status_code == 409
    again = await take(app)
    assert again["request_id"] == first and db.get_request(first).attempt_id != attempt

    age_delivery(db, first)
    await take(app)
    async with human_client(app) as human:
        cancelled = (await human.post(f"/api/requests/{first}/delivery/cancel")).json()
        assert cancelled["status"] == "CANCELLED" and cancelled["delivery"] == "cancelled"
        # Withdrawing a message still waiting works too.
        assert (await human.post(f"/api/requests/{second}/delivery/cancel")).status_code == 200
    async with agent_client(app, "tok-claude") as claude:
        assert (await claude.post(f"/api/requests/{first}/delivery/retry")).status_code == 403


@pytest.mark.asyncio
async def test_a_late_confirmation_settles_an_uncertain_delivery(setup):  # noqa: F811
    app, db = setup
    await start(app, "claude", "c1", "claude-code")
    req_id = (await write(app, "c1")).json()["id"]
    await take(app)
    age_delivery(db, req_id)
    await take(app)
    assert aspects(db, req_id)[0] == "uncertain"
    await event(app, "claude", "c1", event="prompt", request_id=req_id)
    assert aspects(db, req_id) == ("accepted", "started", "none")


@pytest.mark.asyncio
async def test_bridge_timeout_is_uncertain_then_found_in_the_conversation(setup, monkeypatch):  # noqa: F811
    app, db = setup
    await start(app, "codex", "cx1", "codex")
    req_id = (await write(app, "cx1", "per codex")).json()["id"]
    codex = MagicMock()
    codex.send.side_effect = ChannelUncertain("codex queue did not answer within 60 s")
    daemon = BridgeDaemon(store=HttpStore(token="tok-bridge", client=TestClient(app)),
                          tmux=MagicMock(), channels={"codex": codex})
    pages = {"messages": []}
    monkeypatch.setattr(bridge_daemon, "read_transcript", lambda *a, **k: pages)
    daemon.run_loop_step()
    req = db.get_request(req_id)
    assert (req.status, req.delivery, req.delivery_channel) == (RequestStatus.DELIVERING, "uncertain", "bridge")
    assert codex.send.call_count == 1

    daemon.run_loop_step()  # not in the conversation yet, and not sent again
    assert codex.send.call_count == 1 and aspects(db, req_id)[0] == "uncertain"
    pages["messages"] = [{"role": "user", "text": "per codex", "via": "placet", "request_id": req_id}]
    daemon._reconciled_at.clear()
    daemon.run_loop_step()
    req = db.get_request(req_id)
    assert (req.status, req.delivery) == (RequestStatus.EXECUTING, "accepted")
    assert "acked" in [e.event for e in db.list_events(req_id)]


@pytest.mark.asyncio
async def test_expiry_cancels_a_pending_delivery(setup):  # noqa: F811
    app, db = setup
    await start(app, "claude", "c1", "claude-code")
    req_id = (await write(app, "c1")).json()["id"]
    with db._get_connection() as conn:
        conn.execute("UPDATE requests SET expires_at = ? WHERE id = ?",
                     ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), req_id))
    db.expire_stale_requests()
    assert db.get_request(req_id).delivery == "cancelled"


def test_transcripts_mark_the_messages_placet_delivered():
    from bridge.transcripts import _placet_or_user
    assert _placet_or_user("[Placet Request #12 dall'utente]: ciao", None)["request_id"] == 12
    assert _placet_or_user("[Placet Request #7 da codex]: rivedi", None)["request_id"] == 7
    assert "request_id" not in _placet_or_user("ciao", None)


# --- Time never frees a session's queue; the user does ---------------------------------


def age_acceptance(db, req_id, seconds):
    then = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
    with db._get_connection() as conn:
        conn.execute("UPDATE requests SET delivery_at = ?, executing_since = ? WHERE id = ?",
                     (then, then, req_id))


async def bridge_delivers(app, session_id):
    async with agent_client(app, "tok-bridge") as bridge:
        mail = (await bridge.post(f"/api/bridge/sessions/{session_id}/take")).json()
        await bridge.post(f"/api/bridge/requests/{mail['request_id']}/injected")
        return mail["request_id"]


@pytest.mark.asyncio
async def test_an_unseen_turn_warns_but_keeps_the_queue(setup):  # noqa: F811
    app, db = setup
    await start(app, "codex", "cx1", "codex")
    first = (await write(app, "cx1", "lungo lavoro")).json()["id"]
    await write(app, "cx1", "dopo")
    assert await bridge_delivers(app, "cx1") == first
    age_acceptance(db, first, 3600)
    assert db.unconfirmed_starts() == [first]
    assert db.unconfirmed_starts() == []  # once
    assert db.get_request(first).start_unconfirmed_at is not None
    assert db.bridge_sessions_with_mail() == []  # an hour later, still waiting
    # Stalled is a warning too: the queue does not move.
    db.stall_executing(lambda agent: 60)
    assert db.get_request(first).status == RequestStatus.STALLED
    assert db.request_in_flight("cx1") == first


@pytest.mark.asyncio
async def test_the_user_moves_the_queue_on(setup):  # noqa: F811
    app, db = setup
    await start(app, "codex", "cx1", "codex")
    first = (await write(app, "cx1", "uno")).json()["id"]
    second = (await write(app, "cx1", "due")).json()["id"]
    third = (await write(app, "cx1", "tre")).json()["id"]
    await bridge_delivers(app, "cx1")
    async with human_client(app) as human:
        bad = await human.post(f"/api/requests/{second}/queue/release", json={"reason": "finished"})
        assert bad.status_code == 409  # not delivered: nothing to release
        resp = await human.post(f"/api/requests/{first}/queue/release", json={"reason": "finished"})
        assert resp.status_code == 200 and resp.json()["queue_released_at"]
        # The user's statement, not an observed end.
        assert resp.json()["execution"] == "unobserved"
    assert "marked_finished_by_user" in [e.event for e in db.list_events(first)]
    assert await bridge_delivers(app, "cx1") == second
    async with human_client(app) as human:
        await human.post(f"/api/requests/{second}/queue/release", json={"reason": "queue_next"})
    assert "queue_overridden" in [e.event for e in db.list_events(second)]
    assert await bridge_delivers(app, "cx1") == third
    async with agent_client(app, "tok-codex") as codex:
        assert (await codex.post(f"/api/requests/{third}/queue/release",
                                 json={"reason": "finished"})).status_code == 403


@pytest.mark.asyncio
async def test_cancelling_a_delivered_message_frees_the_queue_only(setup):  # noqa: F811
    app, db = setup
    await start(app, "codex", "cx1", "codex")
    first = (await write(app, "cx1", "uno")).json()["id"]
    second = (await write(app, "cx1", "due")).json()["id"]
    await bridge_delivers(app, "cx1")
    async with human_client(app) as human:
        resp = (await human.post(f"/api/requests/{first}/delivery/cancel")).json()
    assert resp["status"] == "CANCELLED" and resp["delivery"] == "accepted"
    assert await bridge_delivers(app, "cx1") == second


@pytest.mark.asyncio
async def test_hooks_record_who_saw_the_execution(setup):  # noqa: F811
    app, db = setup
    await start(app, "claude", "c1", "claude-code")
    req_id = (await write(app, "c1")).json()["id"]
    await take(app)
    await event(app, "claude", "c1", event="prompt", request_id=req_id, adapter="claude_code.py/2")
    assert db.get_request(req_id).execution_source == "claude-code claude_code.py/2"
    started = [e for e in db.list_events(req_id) if e.event == "execution_started"][0]
    assert started.details == {"source": "claude-code claude_code.py/2"}


def test_older_requests_do_not_hold_a_queue(tmp_path):
    import sqlite3
    from backend.app.database import Database
    path = tmp_path / "old.db"
    db = Database(str(path))
    db.init_db()
    with sqlite3.connect(path) as conn:  # as before this version
        for col in ("queue_released_at", "start_unconfirmed_at", "execution_source"):
            conn.execute(f"ALTER TABLE requests DROP COLUMN {col}")
        conn.execute(
            "INSERT INTO requests (sender_id, recipient_id, question_type, prompt, status, created_at,"
            " recipient_session_id) VALUES ('human', 'codex', 'message', 'x', 'COMPLETED', '2026-01-01', 'cx1')")
    Database(str(path)).init_db()
    assert Database(str(path)).request_in_flight("cx1") is None


@pytest.mark.asyncio
async def test_the_users_messages_have_no_outcome_to_deliver(setup):  # noqa: F811
    app, db = setup
    await start(app, "claude", "c1", "claude-code")
    req_id = (await write(app, "c1")).json()["id"]
    await take(app)
    await event(app, "claude", "c1", event="prompt", request_id=req_id)
    await event(app, "claude", "c1", event="stop", last_message="ok")
    now = datetime.now(timezone.utc)
    assert db.list_undelivered_results(now + timedelta(hours=1), now - timedelta(hours=1)) == []
