"""RFC-02: stable named nodes that survive /clear and reopening."""
import pytest

from backend.app.database import Database
from backend.app.main import create_app
from tests.helpers import agent_client, human_client
from tests.test_session_messages import CONFIG


@pytest.fixture
def app(tmp_path):
    db = Database(str(tmp_path / "placet.db"))
    db.init_db()
    return create_app(db=db, config=CONFIG), db


async def event(app, session_id, body, agent="claude"):
    async with agent_client(app, f"tok-{agent}") as client:
        resp = await client.post(f"/api/sessions/{session_id}/events", json=body)
        assert resp.status_code == 200
        return resp.json()


START = {"event": "start", "provider": "claude-code", "cwd": "/home/u/proj"}


@pytest.mark.asyncio
async def test_naming_a_session_creates_a_node(app):
    app, db = app
    await event(app, "s1", START)
    async with human_client(app) as human:
        resp = await human.post("/api/nodes", json={"name": "backend dev", "session_id": "s1"})
        assert resp.status_code == 201
        node = resp.json()
        assert node["provider"] == "claude-code" and node["cwd"] == "/home/u/proj"
        assert node["session_id"] == "s1"
        session = (await human.get("/api/sessions")).json()[0]
        assert session["node_name"] == "backend dev"
        # Names ignore case and are unique.
        resp = await human.post("/api/nodes", json={"name": "Backend Dev", "session_id": "s1"})
        assert resp.status_code == 409 and resp.json()["code"] == "node.name_taken"
        assert (await human.post("/api/nodes", json={"name": "a/b", "session_id": "s1"})).status_code == 422
        assert (await human.post("/api/nodes", json={"name": "x", "session_id": "nope"})).status_code == 404


@pytest.mark.asyncio
async def test_clear_moves_the_node_to_the_new_conversation(app):
    app, db = app
    await event(app, "old", START)
    node = db.create_node("reviewer", "old")
    await event(app, "old", {"event": "end", "provider": "claude-code"})
    await event(app, "new", {**START, "start_source": "clear"})
    assert db.get_session("new").node_id == node.id
    assert db.get_node(node.id).session_id == "new"


@pytest.mark.asyncio
async def test_clear_does_not_take_unrelated_nodes(app):
    app, db = app
    await event(app, "old", START)
    db.create_node("reviewer", "old")
    await event(app, "old", {"event": "end", "provider": "claude-code"})
    # A plain start, another folder or another agent never inherit the node.
    await event(app, "fresh", START)
    await event(app, "elsewhere", {**START, "cwd": "/home/u/other", "start_source": "clear"})
    await event(app, "codex1", {"event": "start", "provider": "codex", "cwd": "/home/u/proj",
                                "start_source": "clear"}, agent="codex")
    for sid in ("fresh", "elsewhere", "codex1"):
        assert db.get_session(sid).node_id is None
    # Once a new conversation holds the node, a second clear does not take it.
    await event(app, "new", {**START, "start_source": "clear"})
    await event(app, "other", {**START, "start_source": "clear"})
    assert db.get_session("new").node_id is not None
    assert db.get_session("other").node_id is None


@pytest.mark.asyncio
async def test_attach_rename_and_delete(app):
    app, db = app
    await event(app, "s1", START)
    await event(app, "s2", START)
    await event(app, "c1", {"event": "start", "provider": "codex"}, agent="codex")
    node = db.create_node("main", "s1")
    async with human_client(app) as human:
        resp = await human.put("/api/sessions/s2/node", json={"node_id": node.id})
        assert resp.status_code == 200 and resp.json()["node_name"] == "main"
        resp = await human.put("/api/sessions/c1/node", json={"node_id": node.id})
        assert resp.status_code == 409 and resp.json()["code"] == "node.other_provider"
        assert (await human.put("/api/sessions/s2/node", json={"node_id": 999})).status_code == 404
        resp = await human.patch(f"/api/nodes/{node.id}", json={"name": "core"})
        assert resp.status_code == 200 and resp.json()["name"] == "core"
        assert [n["name"] for n in (await human.get("/api/nodes")).json()] == ["core"]
        resp = await human.put("/api/sessions/s2/node", json={"node_id": None})
        assert resp.json()["node_id"] is None
        assert (await human.delete(f"/api/nodes/{node.id}")).status_code == 204
        assert db.get_session("s1").node_id is None
        assert (await human.get("/api/nodes")).json() == []


@pytest.mark.asyncio
async def test_agents_cannot_manage_nodes(app):
    app, db = app
    await event(app, "s1", START)
    async with agent_client(app, "tok-claude") as client:
        assert (await client.get("/api/nodes")).status_code in (401, 403, 404)
        resp = await client.post("/api/nodes", json={"name": "x", "session_id": "s1"})
        assert resp.status_code in (401, 403, 404)
