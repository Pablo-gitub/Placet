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
