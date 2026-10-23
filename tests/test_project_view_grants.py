"""RFC-17 part 1: who may see a project as a coordinator is a per-project, per-node grant the human sets."""
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
    return create_app(db=db, config=CONFIG, bridge_token="tok-bridge"), db


async def world(app, db):
    """Project P with a PM node (claude) and a developer node (codex); project Q shares the developer."""
    for agent, sid, provider in (("claude", "pm-s", "claude-code"), ("codex", "dev-s", "codex")):
        async with agent_client(app, f"tok-{agent}") as client:
            await client.post(f"/api/sessions/{sid}/events", json={"event": "start", "provider": provider, "cwd": "/p"})
    pm, dev = db.create_node("pm", "pm-s"), db.create_node("dev", "dev-s")
    p, q = db.create_project("P"), db.create_project("Q")
    for project in (p, q):
        db.add_node_to_project(project.id, dev.id)
    db.add_node_to_project(p.id, pm.id)
    return p, q, pm, dev


async def put(app, path, **body):
    async with human_client(app) as human:
        return await human.put(path, json=body) if body else await human.put(path)


@pytest.mark.asyncio
async def test_a_grant_is_off_by_default_and_listed_per_project(env):
    app, db = env
    p, q, pm, dev = await world(app, db)
    async with human_client(app) as human:
        assert (await human.get(f"/api/projects/{p.id}/view-grants")).json() == []
        assert db.has_view_grant(p.id, pm.id) is False
        resp = await human.put(f"/api/projects/{p.id}/nodes/{pm.id}/view-grant")
        assert resp.status_code == 200 and resp.json()["active"] is True
        rows = (await human.get(f"/api/projects/{p.id}/view-grants")).json()
        assert [(r["node_id"], r["node_name"], r["active"]) for r in rows] == [(pm.id, "pm", True)]
        assert rows[0]["granted_by"].startswith("human:") and rows[0]["revoked_at"] is None
        assert (await human.get(f"/api/projects/{q.id}/view-grants")).json() == []   # per project, not per node
    assert db.has_view_grant(p.id, pm.id) is True and db.has_view_grant(q.id, pm.id) is False


@pytest.mark.asyncio
async def test_granting_twice_keeps_one_active_grant(env):
    app, db = env
    p, q, pm, dev = await world(app, db)
    await put(app, f"/api/projects/{p.id}/nodes/{pm.id}/view-grant")
    await put(app, f"/api/projects/{p.id}/nodes/{pm.id}/view-grant")
    async with human_client(app) as human:
        rows = (await human.get(f"/api/projects/{p.id}/view-grants")).json()
    assert [r["active"] for r in rows] == [True]


@pytest.mark.asyncio
async def test_revoking_ends_the_grant_and_keeps_the_history(env):
    app, db = env
    p, q, pm, dev = await world(app, db)
    await put(app, f"/api/projects/{p.id}/nodes/{pm.id}/view-grant")
    async with human_client(app) as human:
        assert (await human.delete(f"/api/projects/{p.id}/nodes/{pm.id}/view-grant")).status_code == 200
        rows = (await human.get(f"/api/projects/{p.id}/view-grants")).json()
        assert [(r["active"], r["revoked_at"] is not None) for r in rows] == [(False, True)]
        assert (await human.delete(f"/api/projects/{p.id}/nodes/{pm.id}/view-grant")).status_code == 404   # nothing to revoke
    assert db.has_view_grant(p.id, pm.id) is False
    await put(app, f"/api/projects/{p.id}/nodes/{pm.id}/view-grant")                  # a new, separate grant
    assert db.has_view_grant(p.id, pm.id) is True


@pytest.mark.asyncio
async def test_only_the_human_grants_or_revokes(env):
    app, db = env
    p, q, pm, dev = await world(app, db)
    async with agent_client(app, "tok-claude") as agent:
        assert (await agent.put(f"/api/projects/{p.id}/nodes/{pm.id}/view-grant")).status_code == 403
        assert (await agent.delete(f"/api/projects/{p.id}/nodes/{pm.id}/view-grant")).status_code == 403
        assert (await agent.get(f"/api/projects/{p.id}/view-grants")).status_code == 403
        assert (await agent.put(f"/api/projects/{p.id}/nodes/{pm.id}/public-role", json={"text": "boss"})).status_code == 403
    assert db.has_view_grant(p.id, pm.id) is False


@pytest.mark.asyncio
async def test_a_grant_needs_a_node_of_that_project(env):
    app, db = env
    p, q, pm, dev = await world(app, db)
    assert (await put(app, f"/api/projects/{q.id}/nodes/{pm.id}/view-grant")).status_code == 404   # pm is not in Q
    assert (await put(app, f"/api/projects/999/nodes/{pm.id}/view-grant")).status_code == 404
    assert (await put(app, f"/api/projects/{p.id}/nodes/999/view-grant")).status_code == 404


@pytest.mark.asyncio
async def test_leaving_the_project_or_deleting_it_ends_the_grant(env):
    app, db = env
    p, q, pm, dev = await world(app, db)
    await put(app, f"/api/projects/{p.id}/nodes/{pm.id}/view-grant")
    async with human_client(app) as human:
        assert (await human.delete(f"/api/projects/{p.id}/nodes/{pm.id}")).status_code == 200
    assert db.has_view_grant(p.id, pm.id) is False
    db.add_node_to_project(p.id, pm.id)
    assert db.has_view_grant(p.id, pm.id) is False                                     # coming back does not revive it
    await put(app, f"/api/projects/{q.id}/nodes/{dev.id}/view-grant")
    async with human_client(app) as human:
        assert (await human.delete(f"/api/projects/{q.id}")).status_code == 204
    assert db.has_view_grant(q.id, dev.id) is False


@pytest.mark.asyncio
async def test_the_public_role_is_separate_from_the_private_instructions(env):
    app, db = env
    p, q, pm, dev = await world(app, db)
    async with human_client(app) as human:
        await human.patch(f"/api/projects/{p.id}/nodes/{pm.id}", json={"instructions": "PRIVATE-INSTRUCTIONS"})
        resp = await human.put(f"/api/projects/{p.id}/nodes/{pm.id}/public-role", json={"text": "  Plans the work and assigns it.  "})
        assert resp.status_code == 200 and resp.json()["text"] == "Plans the work and assigns it."
        detail = (await human.get(f"/api/projects/{p.id}")).json()
        node = next(n for n in detail["nodes"] if n["id"] == pm.id)
        assert node["public_role"] == "Plans the work and assigns it."
        assert node["instructions"] == "PRIVATE-INSTRUCTIONS"
        assert node["coordinator_view"] is False
        await human.put(f"/api/projects/{p.id}/nodes/{pm.id}/view-grant")
        node = next(n for n in (await human.get(f"/api/projects/{p.id}")).json()["nodes"] if n["id"] == pm.id)
        assert node["coordinator_view"] is True
        # The role is per project–node: the same node in another project has none.
        other = next(n for n in (await human.get(f"/api/projects/{q.id}")).json()["nodes"] if n["id"] == dev.id)
        assert other["public_role"] is None


@pytest.mark.asyncio
async def test_the_public_role_is_capped_and_can_be_cleared(env):
    app, db = env
    p, q, pm, dev = await world(app, db)
    assert (await put(app, f"/api/projects/{p.id}/nodes/{pm.id}/public-role", text="x" * 500)).status_code == 200
    assert (await put(app, f"/api/projects/{p.id}/nodes/{pm.id}/public-role", text="x" * 501)).status_code == 422
    assert (await put(app, f"/api/projects/{p.id}/nodes/{pm.id}/public-role", text="   ")).json()["text"] is None
    assert db.get_public_role(p.id, pm.id) is None
    assert (await put(app, f"/api/projects/{q.id}/nodes/{pm.id}/public-role", text="x")).status_code == 404


@pytest.mark.asyncio
async def test_every_change_is_audited_without_the_role_text(env):
    app, db = env
    p, q, pm, dev = await world(app, db)
    await put(app, f"/api/projects/{p.id}/nodes/{pm.id}/view-grant")
    await put(app, f"/api/projects/{p.id}/nodes/{pm.id}/public-role", text="SECRET-WORDING")
    async with human_client(app) as human:
        await human.delete(f"/api/projects/{p.id}/nodes/{pm.id}/view-grant")
        events = (await human.get(f"/api/projects/{p.id}/view-events")).json()
    assert [e["event"] for e in events] == ["revoked", "public_role_set", "granted"]      # newest first
    assert all(e["actor"].startswith("human:") and e["node_id"] == pm.id for e in events)
    assert "SECRET-WORDING" not in str(events)
