"""
A request addressed to an agent without a session never lands in a stale or
foreign session: it is bound at creation to the one reachable session of the
sender's folder, or refused with the candidates.
"""
import pytest

from backend.app.config import AgentSpec, PlacetConfig
from backend.app.database import Database
from backend.app.main import create_app
from tests.helpers import agent_client

CONFIG = PlacetConfig(agents={
    "claude": AgentSpec(tmux_session="agent-claude", token="tok-claude", allowed_targets=["human", "codex", "claude"]),
    "codex": AgentSpec(tmux_session="agent-codex", token="tok-codex", allowed_targets=["human", "claude"]),
})


@pytest.fixture
def env(tmp_path):
    db = Database(str(tmp_path / "placet.db"))
    db.init_db()
    return create_app(db=db, config=CONFIG), db


async def start(app, agent, session_id, provider, cwd):
    async with agent_client(app, f"tok-{agent}") as client:
        body = {"event": "start", "provider": provider, "cwd": cwd}
        assert (await client.post(f"/api/sessions/{session_id}/events", json=body)).status_code == 200


async def send(app, **body):
    async with agent_client(app, "tok-claude") as client:
        return await client.post("/api/requests", json={"recipient_id": "codex", "prompt": "do it", **body})


@pytest.mark.asyncio
async def test_the_session_of_the_senders_folder_wins_over_a_stale_foreign_one(env):
    app, db = env
    await start(app, "codex", "stale", "codex", "/home/p/placet-hook-lab")
    await start(app, "codex", "active", "codex", "/home/p/placet")
    await start(app, "claude", "cl", "claude-code", "/home/p/placet")
    resp = await send(app, sender_session_id="cl")
    assert resp.status_code == 201, resp.text
    assert resp.json()["recipient_session_id"] == "active"


@pytest.mark.asyncio
async def test_several_sessions_in_the_folder_are_ambiguous_and_nothing_is_created(env):
    app, db = env
    await start(app, "codex", "a", "codex", "/home/p/placet")
    await start(app, "codex", "b", "codex", "/home/p/placet")
    await start(app, "claude", "cl", "claude-code", "/home/p/placet")
    resp = await send(app, sender_session_id="cl")
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "request.target_session_ambiguous"
    assert sorted(c["id"] for c in body["params"]["candidates"]) == ["a", "b"]
    assert {"id", "title", "cwd"} <= set(body["params"]["candidates"][0])
    assert "target_session" in body["detail"]
    assert db.list_requests() == []


@pytest.mark.asyncio
async def test_no_session_in_the_folder_is_refused_too(env):
    app, db = env
    await start(app, "codex", "elsewhere", "codex", "/home/p/other")
    await start(app, "claude", "cl", "claude-code", "/home/p/placet")
    resp = await send(app, sender_session_id="cl")
    assert resp.status_code == 409 and resp.json()["code"] == "request.target_session_ambiguous"
    assert resp.json()["params"]["candidates"] == []
    assert db.list_requests() == []


@pytest.mark.asyncio
async def test_an_unknown_sender_falls_back_to_the_reachable_sessions(env):
    app, db = env
    await start(app, "codex", "only", "codex", "/home/p/placet")
    resp = await send(app)
    assert resp.status_code == 201 and resp.json()["recipient_session_id"] == "only"
    await start(app, "codex", "second", "codex", "/home/p/lab")
    resp = await send(app)
    assert resp.status_code == 409 and resp.json()["code"] == "request.target_session_ambiguous"
    assert len(db.list_requests()) == 1


@pytest.mark.asyncio
async def test_ended_sessions_are_not_candidates(env):
    app, db = env
    await start(app, "codex", "gone", "codex", "/home/p/placet")
    await start(app, "codex", "live", "codex", "/home/p/placet")
    db.end_session("gone")
    await start(app, "claude", "cl", "claude-code", "/home/p/placet")
    resp = await send(app, sender_session_id="cl")
    assert resp.status_code == 201 and resp.json()["recipient_session_id"] == "live"


@pytest.mark.asyncio
async def test_an_explicit_session_or_node_is_left_alone(env):
    app, db = env
    await start(app, "codex", "stale", "codex", "/home/p/lab")
    await start(app, "codex", "active", "codex", "/home/p/placet")
    await start(app, "claude", "cl", "claude-code", "/home/p/placet")
    resp = await send(app, sender_session_id="cl", recipient_session_id="stale")
    assert resp.status_code == 201 and resp.json()["recipient_session_id"] == "stale"
    db.create_node("planner", "cl")
    db.create_node("builder", "stale")
    resp = await send(app, sender_session_id="cl", recipient_id=None, target_node="builder")
    assert resp.status_code != 409 or resp.json()["code"] != "request.target_session_ambiguous"
    assert resp.status_code == 409 and resp.json()["code"] == "relation.not_found"  # routing did not interfere


@pytest.mark.asyncio
async def test_an_agent_with_no_reachable_session_still_takes_the_request_unbound(env):
    app, db = env
    resp = await send(app)  # nothing to choose among: it waits for the agent as before
    assert resp.status_code == 201 and resp.json()["recipient_session_id"] is None


def test_the_mcp_tool_tells_the_agent_to_pass_a_target_session():
    import json
    from unittest.mock import MagicMock, patch
    import httpx
    from mcp_server.server import placet_send_to_agent

    body = {"code": "request.target_session_ambiguous", "detail": "2 reachable codex sessions. Pass target_session.",
            "params": {"candidates": [{"id": "a", "title": None, "cwd": "/p"}, {"id": "b", "title": None, "cwd": "/p"}]}}
    resp = MagicMock(status_code=409)
    resp.json.return_value = body
    error = httpx.HTTPStatusError("Conflict", request=MagicMock(), response=resp)
    with patch("httpx.Client.post", side_effect=error):
        out = json.loads(placet_send_to_agent("codex", "do it"))
    assert out["status"] == "ERROR" and out["code"] == 409
    assert out["product_code"] == "request.target_session_ambiguous"
    assert [c["id"] for c in out["params"]["candidates"]] == ["a", "b"]
    assert "target_session" in out["detail"]


async def send_as_claude(app, **body):
    async with agent_client(app, "tok-claude") as client:
        return await client.post("/api/requests", json={"recipient_id": "claude", "prompt": "do it", **body})


@pytest.mark.asyncio
async def test_the_sender_is_never_its_own_candidate(env):
    app, db = env
    await start(app, "claude", "me", "claude-code", "/home/p/placet")
    await start(app, "claude", "other", "claude-code", "/home/p/placet")
    db.touch_waiter("me")  # Claude sessions are reachable while their waiter listens
    db.touch_waiter("other")
    resp = await send_as_claude(app, sender_session_id="me")
    assert resp.status_code == 201, resp.text
    assert resp.json()["recipient_session_id"] == "other"


@pytest.mark.asyncio
async def test_only_the_sender_means_no_reachable_session(env):
    app, db = env
    await start(app, "claude", "me", "claude-code", "/home/p/placet")
    db.touch_waiter("me")
    resp = await send_as_claude(app, sender_session_id="me")
    assert resp.status_code == 201, resp.text
    assert resp.json()["recipient_session_id"] is None  # unbound, as before
