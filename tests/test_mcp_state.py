"""
RF-27 backend: what the hook reported about the Placet MCP server, and a
second, independent signal when a delivered request's turn ends without the
session ever having used it.
"""
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
    assert session.mcp_state == "configured" and session.mcp_seen_at is not None
    await start(app, mcp="missing")  # a later start cannot deny what was seen
    assert db.get_session("c1").mcp_state == "configured"


@pytest.mark.asyncio
async def test_a_turn_end_without_any_mcp_call_is_suspect_only_when_the_config_is_unknown(setup):  # noqa: F811
    app, db = setup
    await start(app, "c1", mcp="unknown")
    await start(app, "c2", mcp="configured")
    await start(app, "c3")  # nothing reported
    for sid in ("c1", "c2", "c3"):
        await delivered(app, sid)
        await event(app, "claude", sid, event="stop", last_message="fatto")
    assert db.get_session("c1").mcp_state == "suspect"
    assert db.get_session("c2").mcp_state == "configured"
    assert db.get_session("c3").mcp_state is None


@pytest.mark.asyncio
async def test_a_session_that_used_the_mcp_is_never_suspect(setup):  # noqa: F811
    app, db = setup
    await start(app, mcp="unknown")
    req_id = await delivered(app)
    async with agent_client(app, "tok-claude") as claude:
        await claude.post(f"/api/requests/{req_id}/reply", json={"content": "ok"})
    await event(app, "claude", "c1", event="stop", last_message="fatto")
    assert db.get_session("c1").mcp_state == "configured"


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
    assert db.get_session("c1").mcp_state == "configured"
