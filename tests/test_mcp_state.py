"""
RF-27 backend: what the hook reported about the Placet MCP server, and a
second, independent signal when a delivered request's turn ends without the
session ever having used it.
"""
import asyncio

import pytest

from tests.helpers import agent_client, human_client
from tests.test_request_aspects import event, take
from tests.test_session_messages import setup, write  # noqa: F401


async def start(app, session_id="c1", mcp=None, provider="claude-code"):
    body = {"event": "start", "provider": provider, "cwd": "/p"}
    if mcp is not None:
        body["mcp"] = mcp
    await event(app, "claude", session_id, **body)


async def delivered(app, session_id="c1"):
    req_id = (await write(app, session_id, "fai")).json()["id"]
    await take(app, session_id)
    await event(app, "claude", session_id, event="prompt", request_id=req_id)
    return req_id


@pytest.mark.asyncio
async def test_the_state_reported_at_start_is_stored_and_listed(setup):  # noqa: F811
    app, db = setup
    await start(app, mcp="missing")
    session = db.get_session("c1")
    assert session.mcp_state == "missing" and session.mcp_seen_at is None
    async with human_client(app) as human:
        listed = {s["id"]: s for s in (await human.get("/api/sessions")).json()}
    assert listed["c1"]["mcp_state"] == "missing"
    await start(app, "c2")  # an older hook says nothing
    assert db.get_session("c2").mcp_state is None


@pytest.mark.asyncio
async def test_a_new_start_replaces_the_state(setup):  # noqa: F811
    app, db = setup
    await start(app, mcp="missing")
    await start(app, mcp="configured")
    assert db.get_session("c1").mcp_state == "configured"


@pytest.mark.asyncio
async def test_using_the_mcp_proves_the_connection(setup):  # noqa: F811
    app, db = setup
    await start(app, mcp="missing")
    req_id = await delivered(app)
    async with agent_client(app, "tok-claude") as claude:
        assert (await claude.post(f"/api/requests/{req_id}/progress", json={"content": "in corso"})).status_code == 200
    session = db.get_session("c1")
    assert session.mcp_state == "missing" and session.mcp_seen_at is not None   # the use is evidence, not configuration
    await start(app, mcp="missing")                                            # a new start reports the configuration anew
    assert db.get_session("c1").mcp_state == "missing" and db.get_session("c1").mcp_seen_at is not None


@pytest.mark.asyncio
async def test_a_turn_end_without_any_mcp_call_proves_nothing_about_the_connection(setup):  # noqa: F811
    """
    A healthy session never needs an MCP call: messages from the phone are delivered by the waiter and
    the reply is taken from the transcript, and agent requests accept implicit replies (RF-25).
    """
    app, db = setup
    await start(app, "c1", mcp="unknown")
    await start(app, "c2", mcp="configured")
    await start(app, "c3")  # nothing reported
    await start(app, "c4", mcp="missing")
    for sid in ("c1", "c2", "c3", "c4"):
        await delivered(app, sid)
        await event(app, "claude", sid, event="stop", last_message="fatto")
    states = {sid: db.get_session(sid).mcp_state for sid in ("c1", "c2", "c3", "c4")}
    assert states == {"c1": "unknown", "c2": "configured", "c3": None, "c4": "missing"}


def test_there_is_no_turn_end_heuristic_left(setup):  # noqa: F811
    _, db = setup
    assert not hasattr(db, "suspect_mcp")


def test_old_suspect_rows_become_unknown_once(tmp_path):
    from backend.app.database import Database
    path = str(tmp_path / "placet.db")
    db = Database(path)
    db.init_db()
    now = "2026-10-22T10:00:00+00:00"
    with db._get_connection() as conn:
        for sid, state in (("s-suspect", "suspect"), ("s-missing", "missing"), ("s-conf", "configured"), ("s-none", None)):
            conn.execute("INSERT INTO sessions (id, agent_id, provider, state, started_at, last_seen_at, mcp_state)"
                         " VALUES (?, 'claude', 'claude-code', 'idle', ?, ?, ?)", (sid, now, now, state))
    for _ in range(2):                                   # idempotent
        reopened = Database(path)
        reopened.init_db()
        assert {sid: reopened.get_session(sid).mcp_state for sid in ("s-suspect", "s-missing", "s-conf", "s-none")} == {
            "s-suspect": "unknown", "s-missing": "missing", "s-conf": "configured", "s-none": None}


@pytest.mark.asyncio
async def test_a_session_that_used_the_mcp_keeps_its_configuration(setup):  # noqa: F811
    app, db = setup
    await start(app, mcp="unknown")
    req_id = await delivered(app)
    async with agent_client(app, "tok-claude") as claude:
        await claude.post(f"/api/requests/{req_id}/reply", json={"content": "ok"})
    await event(app, "claude", "c1", event="stop", last_message="fatto")
    assert db.get_session("c1").mcp_state == "unknown" and db.get_session("c1").mcp_seen_at is not None


@pytest.mark.asyncio
async def test_a_turn_without_a_placet_request_proves_nothing(setup):  # noqa: F811
    app, db = setup
    await start(app, mcp="unknown")
    await event(app, "claude", "c1", event="prompt")
    await event(app, "claude", "c1", event="stop", last_message="ciao")
    assert db.get_session("c1").mcp_state == "unknown"


@pytest.mark.asyncio
async def test_sending_a_request_from_a_session_proves_the_connection(setup):  # noqa: F811
    app, db = setup
    await start(app, mcp="missing")
    async with agent_client(app, "tok-claude") as claude:
        resp = await claude.post("/api/requests", json={
            "recipient_id": "human", "question_type": "confirmation", "prompt": "ok?", "sender_session_id": "c1"})
        assert resp.status_code == 201, resp.text
    session = db.get_session("c1")
    assert session.mcp_state == "missing" and session.mcp_seen_at is not None      # use is recorded apart


# --- configuration and use are kept apart ----------------------------------------------------


@pytest.mark.asyncio
async def test_each_start_records_when_the_configuration_was_checked(setup):  # noqa: F811
    app, db = setup
    await start(app, mcp="configured")
    first = db.get_session("c1").mcp_checked_at
    assert first is not None
    await start(app, mcp="missing")
    again = db.get_session("c1")
    assert again.mcp_state == "missing" and again.mcp_checked_at >= first
    await start(app, "c2")                                  # an older hook reports nothing
    assert db.get_session("c2").mcp_checked_at is None


@pytest.mark.asyncio
async def test_a_use_before_this_start_does_not_hide_a_missing_configuration(setup):  # noqa: F811
    app, db = setup
    await start(app, mcp="configured")
    db.mark_mcp_seen("c1")
    await asyncio.sleep(0.01)
    await start(app, mcp="missing")                         # restarted without the server
    session = db.get_session("c1")
    assert session.mcp_state == "missing"
    assert session.mcp_seen_at < session.mcp_checked_at     # the use predates this start


@pytest.mark.asyncio
async def test_a_use_before_this_start_leaves_an_unknown_session_unknown(setup):  # noqa: F811
    app, db = setup
    await start(app, mcp="unknown")
    db.mark_mcp_seen("c1")
    await asyncio.sleep(0.01)
    await start(app, mcp="unknown")                         # a new start: seen only before it
    await delivered(app)
    await event(app, "claude", "c1", event="stop", last_message="fatto")
    assert db.get_session("c1").mcp_state == "unknown"


@pytest.mark.asyncio
async def test_a_use_since_this_start_changes_no_state(setup):  # noqa: F811
    app, db = setup
    await start(app, mcp="unknown")
    await asyncio.sleep(0.01)
    db.mark_mcp_seen("c1")
    await delivered(app)
    await event(app, "claude", "c1", event="stop", last_message="fatto")
    assert db.get_session("c1").mcp_state == "unknown"


@pytest.mark.asyncio
async def test_read_only_tools_that_carry_a_session_count_as_seen(setup):  # noqa: F811
    app, db = setup
    await start(app, "c1", mcp="unknown")
    async with agent_client(app, "tok-claude") as claude:
        assert (await claude.get("/api/agents/me/relations", params={"session_id": "c1"})).status_code == 200
    assert db.get_session("c1").mcp_seen_at is not None
    # placet_check_status carries a request id: the caller's own session on it is the one that used the tool.
    await start(app, "c2", mcp="unknown")
    req_id = (await write(app, "c2", "fai")).json()["id"]
    await take(app, "c2")
    await event(app, "claude", "c2", event="prompt", request_id=req_id)
    assert db.get_session("c2").mcp_seen_at is None
    async with agent_client(app, "tok-claude") as claude:
        assert (await claude.get(f"/api/requests/{req_id}")).status_code == 200
    assert db.get_session("c2").mcp_seen_at is not None


@pytest.mark.asyncio
async def test_a_human_or_an_unrelated_agent_reading_a_request_proves_nothing(setup):  # noqa: F811
    app, db = setup
    await start(app, "c1", mcp="unknown")
    req_id = (await write(app, "c1", "fai")).json()["id"]
    async with human_client(app) as human:
        assert (await human.get(f"/api/requests/{req_id}")).status_code == 200
    assert db.get_session("c1").mcp_seen_at is None
