"""RFC-03..07, RFC-13: relation types, relations A -> B and their policies."""
import pytest

from backend.app.config import AgentSpec, PlacetConfig
from backend.app.database import Database
from backend.app.main import create_app
from tests.helpers import agent_client, human_client

CONFIG = PlacetConfig(agents={
    "claude": AgentSpec(tmux_session="agent-claude", token="tok-claude", allowed_targets=["human", "codex"]),
    "codex": AgentSpec(tmux_session="agent-codex", token="tok-codex", allowed_targets=["human", "claude"]),
})


@pytest.fixture
def env(tmp_path):
    db = Database(str(tmp_path / "placet.db"))
    db.init_db()
    return create_app(db=db, config=CONFIG), db


async def event(app, agent, session_id, body):
    async with agent_client(app, f"tok-{agent}") as client:
        assert (await client.post(f"/api/sessions/{session_id}/events", json=body)).status_code == 200


async def two_nodes(app, db):
    """A busy Claude session (the sender) and a Codex one, both named."""
    await event(app, "claude", "cl", {"event": "start", "provider": "claude-code", "cwd": "/p"})
    await event(app, "claude", "cl", {"event": "prompt", "provider": "claude-code"})
    await event(app, "codex", "cx", {"event": "start", "provider": "codex", "cwd": "/p"})
    return db.create_node("planner", "cl"), db.create_node("builder", "cx")


async def send(app, type_id=None, agent="claude", target="codex", session="cx"):
    async with agent_client(app, f"tok-{agent}") as client:
        body = {"recipient_id": target, "prompt": "do it", "recipient_session_id": session}
        if type_id is not None:
            body["relation_type_id"] = type_id
        resp = await client.post("/api/requests", json=body)
        assert resp.status_code == 201, resp.text
        return resp.json()


@pytest.mark.asyncio
async def test_without_relation_delivery_is_manual(env):
    app, db = env
    await two_nodes(app, db)
    req = await send(app)
    assert req["status"] == "PENDING" and req["relation_id"] is None


@pytest.mark.asyncio
async def test_automatic_relation_skips_review_manual_does_not(env):
    app, db = env
    a, b = await two_nodes(app, db)
    review = db.create_relation_type("review", "Ask for a code review")
    rel = db.create_relation(a.id, b.id, review.id, "automatic")
    req = await send(app)
    assert req["status"] == "APPROVED" and req["relation_id"] == rel.id
    assert not req["allow_auto_return"]
    events = db.list_events(req["id"])
    assert any(e.actor == f"system:relation#{rel.id}" for e in events)
    db.set_relation_approval(rel.id, "manual")
    req = await send(app)
    assert req["status"] == "PENDING" and req["relation_id"] == rel.id


@pytest.mark.asyncio
async def test_several_types_need_the_type(env):
    app, db = env
    a, b = await two_nodes(app, db)
    review = db.create_relation_type("review", "")
    build = db.create_relation_type("build", "")
    db.create_relation(a.id, b.id, review.id, "automatic")
    db.create_relation(a.id, b.id, build.id, "manual")
    # Placet never guesses between types: without one, review by the user.
    assert (await send(app))["status"] == "PENDING"
    assert (await send(app, review.id))["status"] == "APPROVED"
    assert (await send(app, build.id))["status"] == "PENDING"


@pytest.mark.asyncio
async def test_reply_follows_the_reverse_relation(env):
    app, db = env
    a, b = await two_nodes(app, db)
    review = db.create_relation_type("review", "")
    db.create_relation(a.id, b.id, review.id, "automatic")
    db.create_relation(b.id, a.id, review.id, "automatic")
    assert (await send(app))["allow_auto_return"] is True
    # A manual relation approved by the user: the reverse one still applies.
    rel = db.find_relation(a.id, b.id)
    db.set_relation_approval(rel.id, "manual")
    req = await send(app)
    async with human_client(app) as human:
        resp = await human.post(f"/api/requests/{req['id']}/decision", json={"status": "APPROVED"})
        assert resp.status_code == 200 and resp.json()["allow_auto_return"] is True


@pytest.mark.asyncio
async def test_relation_api(env):
    app, db = env
    a, b = await two_nodes(app, db)
    async with human_client(app) as human:
        resp = await human.post("/api/relation-types", json={"name": "review", "description": "Code review"})
        assert resp.status_code == 201
        type_id = resp.json()["id"]
        resp = await human.post("/api/relation-types", json={"name": "Review"})
        assert resp.status_code == 409 and resp.json()["code"] == "relation_type.name_taken"
        body = {"from_node": a.id, "to_node": b.id, "type_id": type_id, "approval": "automatic"}
        resp = await human.post("/api/relations", json=body)
        assert resp.status_code == 201
        rel = resp.json()
        assert (rel["from_name"], rel["to_name"], rel["type_name"]) == ("planner", "builder", "review")
        assert (await human.post("/api/relations", json=body)).json()["code"] == "relation.exists"
        resp = await human.post("/api/relations", json={**body, "to_node": a.id})
        assert resp.status_code == 422 and resp.json()["code"] == "relation.self"
        resp = await human.delete(f"/api/relation-types/{type_id}")
        assert resp.status_code == 409 and resp.json()["code"] == "relation_type.in_use"
        resp = await human.patch(f"/api/relations/{rel['id']}", json={"approval": "manual"})
        assert resp.json()["approval"] == "manual"
        assert len((await human.get(f"/api/relations?node_id={b.id}")).json()) == 1
        # Deleting a node removes its relations.
        assert (await human.delete(f"/api/nodes/{b.id}")).status_code == 204
        assert (await human.get("/api/relations")).json() == []
        assert (await human.delete(f"/api/relation-types/{type_id}")).status_code == 204
    async with agent_client(app, "tok-claude") as client:
        assert (await client.get("/api/relations")).status_code in (401, 403, 404)
        assert (await client.post("/api/relation-types", json={"name": "x"})).status_code in (401, 403, 404)
