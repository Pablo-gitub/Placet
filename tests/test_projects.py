"""Tests for projects (RFC-09, RF-44): grouping nodes and relations."""
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
    await event(app, "claude", "cl", {"event": "start", "provider": "claude-code", "cwd": "/p"})
    await event(app, "claude", "cl", {"event": "prompt", "provider": "claude-code"})
    await event(app, "codex", "cx", {"event": "start", "provider": "codex", "cwd": "/p"})
    return db.create_node("planner", "cl"), db.create_node("builder", "cx")


# --- 1. CRUD and duplicate names ---------------------------------------------

@pytest.mark.asyncio
async def test_project_crud_and_duplicate_names(env):
    app, db = env
    async with human_client(app) as client:
        # Create
        res = await client.post("/api/projects", json={
            "name": "Placet Core",
            "description": "Core orchestration and bridge",
            "relation_rule": "Test thoroughly",
            "max_auto_hops": 5,
            "max_auto_per_hour": 15,
        })
        assert res.status_code == 201
        p = res.json()
        assert p["name"] == "Placet Core"
        assert p["description"] == "Core orchestration and bridge"
        assert p["relation_rule"] == "Test thoroughly"
        assert p["max_auto_hops"] == 5
        assert p["max_auto_per_hour"] == 15
        p_id = p["id"]

        # Duplicate name (case-insensitive) -> 409
        res_dup = await client.post("/api/projects", json={"name": "placet core"})
        assert res_dup.status_code == 409

        # List
        res_list = await client.get("/api/projects")
        assert res_list.status_code == 200
        items = res_list.json()
        assert len(items) == 1
        assert items[0]["name"] == "Placet Core"
        assert items[0]["nodes_count"] == 0
        assert items[0]["relations_count"] == 0
        assert items[0]["automatic_relations_count"] == 0

        # Detail
        res_detail = await client.get(f"/api/projects/{p_id}")
        assert res_detail.status_code == 200
        d = res_detail.json()
        assert d["id"] == p_id
        assert d["nodes"] == []
        assert d["relations"] == []

        # Patch
        res_patch = await client.patch(f"/api/projects/{p_id}", json={
            "description": "Updated description",
            "max_auto_hops": 2,
        })
        assert res_patch.status_code == 200
        assert res_patch.json()["description"] == "Updated description"
        assert res_patch.json()["max_auto_hops"] == 2

        # Delete
        res_del = await client.delete(f"/api/projects/{p_id}")
        assert res_del.status_code in (200, 204)

        # 404 after delete
        assert (await client.get(f"/api/projects/{p_id}")).status_code == 404


# --- 2. Multiple membership --------------------------------------------------

@pytest.mark.asyncio
async def test_multiple_membership_nodes_and_relations(env):
    app, db = env
    n1, n2 = await two_nodes(app, db)
    ty = db.create_relation_type("review", "Code review")
    rel = db.create_relation(n1.id, n2.id, ty.id, "automatic")

    async with human_client(app) as client:
        p1 = (await client.post("/api/projects", json={"name": "Project 1"})).json()
        p2 = (await client.post("/api/projects", json={"name": "Project 2"})).json()

        # Add node n1 to both p1 and p2
        res1 = await client.put(f"/api/projects/{p1['id']}/nodes/{n1.id}")
        assert res1.status_code == 200
        res2 = await client.put(f"/api/projects/{p2['id']}/nodes/{n1.id}")
        assert res2.status_code == 200

        # Add relation rel to both p1 and p2
        r_res1 = await client.put(f"/api/projects/{p1['id']}/relations/{rel.id}")
        assert r_res1.status_code == 200
        r_res2 = await client.put(f"/api/projects/{p2['id']}/relations/{rel.id}")
        assert r_res2.status_code == 200

        # Verify details
        d1 = (await client.get(f"/api/projects/{p1['id']}")).json()
        d2 = (await client.get(f"/api/projects/{p2['id']}")).json()

        assert any(n["id"] == n1.id for n in d1["nodes"])
        assert any(n["id"] == n1.id for n in d2["nodes"])
        assert any(r["id"] == rel.id for r in d1["relations"])
        assert any(r["id"] == rel.id for r in d2["relations"])


# --- 3. Explicit cascade on deletion -----------------------------------------

@pytest.mark.asyncio
async def test_deletion_cleanup_without_foreign_key_cascade(env):
    app, db = env
    n1, n2 = await two_nodes(app, db)
    ty = db.create_relation_type("review", "Code review")
    rel = db.create_relation(n1.id, n2.id, ty.id, "automatic")

    async with human_client(app) as client:
        p1 = (await client.post("/api/projects", json={"name": "Project 1"})).json()
        p2 = (await client.post("/api/projects", json={"name": "Project 2"})).json()

        await client.put(f"/api/projects/{p1['id']}/nodes/{n1.id}")
        await client.put(f"/api/projects/{p1['id']}/relations/{rel.id}")
        await client.put(f"/api/projects/{p2['id']}/nodes/{n1.id}")
        await client.put(f"/api/projects/{p2['id']}/relations/{rel.id}")

        # 3a. Delete project 1: nodes and relations remain intact in db and in project 2
        await client.delete(f"/api/projects/{p1['id']}")
        assert db.get_node(n1.id) is not None
        assert db.get_relation(rel.id) is not None

        d2 = (await client.get(f"/api/projects/{p2['id']}")).json()
        assert any(n["id"] == n1.id for n in d2["nodes"])
        assert any(r["id"] == rel.id for r in d2["relations"])

        # 3b. Delete relation: removed from project 2 project_relations
        await client.delete(f"/api/relations/{rel.id}")
        d2_after_rel = (await client.get(f"/api/projects/{p2['id']}")).json()
        assert not any(r["id"] == rel.id for r in d2_after_rel["relations"])

        # Re-create relation for node deletion test
        rel2 = db.create_relation(n1.id, n2.id, ty.id, "automatic")
        await client.put(f"/api/projects/{p2['id']}/relations/{rel2.id}")

        # 3c. Delete node n1: removed from project_nodes and its relations from project_relations
        await client.delete(f"/api/nodes/{n1.id}")
        d2_after_node = (await client.get(f"/api/projects/{p2['id']}")).json()
        assert not any(n["id"] == n1.id for n in d2_after_node["nodes"])
        assert not any(r["id"] == rel2.id for r in d2_after_node["relations"])


# --- 4. shared_with warning (RFC-09) -----------------------------------------

@pytest.mark.asyncio
async def test_shared_with_warning(env):
    app, db = env
    n1, n2 = await two_nodes(app, db)
    ty = db.create_relation_type("review", "Code review")
    rel = db.create_relation(n1.id, n2.id, ty.id, "automatic")

    async with human_client(app) as client:
        p1 = (await client.post("/api/projects", json={"name": "Alpha"})).json()
        p2 = (await client.post("/api/projects", json={"name": "Beta"})).json()

        # First add: no other project has it
        res1 = await client.put(f"/api/projects/{p1['id']}/relations/{rel.id}")
        assert res1.status_code == 200
        assert res1.json().get("shared_with") == []

        # Second add to Beta: Alpha already shares it
        res2 = await client.put(f"/api/projects/{p2['id']}/relations/{rel.id}")
        assert res2.status_code == 200
        assert res2.json().get("shared_with") == ["Alpha"]


# --- 5. Consistent nodes and relations on add/remove -------------------------

@pytest.mark.asyncio
async def test_consistent_nodes_and_relations(env):
    app, db = env
    n1, n2 = await two_nodes(app, db)
    ty = db.create_relation_type("review", "Code review")
    rel = db.create_relation(n1.id, n2.id, ty.id, "automatic")

    async with human_client(app) as client:
        p = (await client.post("/api/projects", json={"name": "Orchestrator"})).json()

        # Adding relation to project automatically adds its from_node and to_node
        res_rel = await client.put(f"/api/projects/{p['id']}/relations/{rel.id}")
        assert res_rel.status_code == 200

        detail = (await client.get(f"/api/projects/{p['id']}")).json()
        node_ids = {n["id"] for n in detail["nodes"]}
        assert n1.id in node_ids
        assert n2.id in node_ids

        # Removing a node from project also removes project relations involving it
        res_del_node = await client.delete(f"/api/projects/{p['id']}/nodes/{n1.id}")
        assert res_del_node.status_code == 200
        # Response informs which relations were removed
        assert rel.id in res_del_node.json().get("removed_relations", [])

        detail_after = (await client.get(f"/api/projects/{p['id']}")).json()
        assert not any(n["id"] == n1.id for n in detail_after["nodes"])
        assert not any(r["id"] == rel.id for r in detail_after["relations"])


# --- 6. Effective limits composition -----------------------------------------

@pytest.mark.asyncio
async def test_effective_limits_composition(env):
    app, db = env
    n1, n2 = await two_nodes(app, db)
    ty = db.create_relation_type("review", "Code review")
    rel = db.create_relation(n1.id, n2.id, ty.id, "automatic")

    # Global limits: default 3 hops, 20 per hour
    policy0 = db.get_relation_effective_policy(rel.id)
    assert policy0["max_hops"] == 3
    assert policy0["max_per_hour"] == 20

    async with human_client(app) as client:
        # P1: hops=5, rate=15
        p1 = (await client.post("/api/projects", json={
            "name": "P1", "max_auto_hops": 5, "max_auto_per_hour": 15
        })).json()
        await client.put(f"/api/projects/{p1['id']}/relations/{rel.id}")

        policy1 = db.get_relation_effective_policy(rel.id)
        assert policy1["max_hops"] == 5
        assert policy1["max_per_hour"] == 15

        # P2: hops=2, rate=25
        p2 = (await client.post("/api/projects", json={
            "name": "P2", "max_auto_hops": 2, "max_auto_per_hour": 25
        })).json()
        await client.put(f"/api/projects/{p2['id']}/relations/{rel.id}")

        policy2 = db.get_relation_effective_policy(rel.id)
        # min(5, 2) = 2, min(15, 25) = 15
        assert policy2["max_hops"] == 2
        assert policy2["max_per_hour"] == 15

        # P3: hops=NULL (inherits global 3), rate=NULL (inherits global 20)
        p3 = (await client.post("/api/projects", json={"name": "P3"})).json()
        # Remove from P2 and add to P3
        await client.delete(f"/api/projects/{p2['id']}/relations/{rel.id}")
        await client.put(f"/api/projects/{p3['id']}/relations/{rel.id}")

        policy3 = db.get_relation_effective_policy(rel.id)
        # Between P1 (5, 15) and P3 (global 3, global 20): min(5, 3) = 3, min(15, 20) = 15
        assert policy3["max_hops"] == 3
        assert policy3["max_per_hour"] == 15


# --- 7. Effective rule composition -------------------------------------------

@pytest.mark.asyncio
async def test_effective_rule_composition(env):
    app, db = env
    n1, n2 = await two_nodes(app, db)
    ty = db.create_relation_type("review", "Code review")
    rel = db.create_relation(n1.id, n2.id, ty.id, "automatic")

    # Global rule set
    db.set_relation_rule("Always ask if deleting data.")

    # Relation in 0 projects -> global rule
    pol0 = db.get_relation_effective_policy(rel.id)
    assert pol0["rule"] == "Always ask if deleting data."

    async with human_client(app) as client:
        p1 = (await client.post("/api/projects", json={
            "name": "Alpha", "relation_rule": "Run linters first."
        })).json()
        await client.put(f"/api/projects/{p1['id']}/relations/{rel.id}")

        # Global rule + P1 rule appended
        pol1 = db.get_relation_effective_policy(rel.id)
        assert pol1["rule"] == "Always ask if deleting data.\n\n[Alpha]\nRun linters first."

        p2 = (await client.post("/api/projects", json={
            "name": "Beta", "relation_rule": "Format with oxlint."
        })).json()
        await client.put(f"/api/projects/{p2['id']}/relations/{rel.id}")

        # Global rule + P1 + P2
        pol2 = db.get_relation_effective_policy(rel.id)
        assert pol2["rule"] == (
            "Always ask if deleting data.\n\n"
            "[Alpha]\nRun linters first.\n\n"
            "[Beta]\nFormat with oxlint."
        )

        # If global rule is empty: only project rules
        db.set_relation_rule("")
        pol_no_global = db.get_relation_effective_policy(rel.id)
        assert pol_no_global["rule"] == "[Alpha]\nRun linters first.\n\n[Beta]\nFormat with oxlint."


# --- 8. Agent inspection /api/agents/me/relations and security ----------------

@pytest.mark.asyncio
async def test_agent_me_relations_and_agent_forbidden_on_projects(env):
    app, db = env
    n1, n2 = await two_nodes(app, db)
    ty = db.create_relation_type("review", "Code review")
    rel = db.create_relation(n1.id, n2.id, ty.id, "automatic")

    async with human_client(app) as client:
        p = (await client.post("/api/projects", json={
            "name": "Gamma", "relation_rule": "Check tests."
        })).json()
        await client.put(f"/api/projects/{p['id']}/relations/{rel.id}")

    # Agent checks relations: /api/agents/me/relations includes projects and effective rule
    async with agent_client(app, "tok-claude") as client:
        res = await client.get("/api/agents/me/relations?session_id=cl")
        assert res.status_code == 200
        data = res.json()
        assert len(data["relations"]) == 1
        r_info = data["relations"][0]
        assert r_info["id"] == rel.id
        assert r_info["projects"] == ["Gamma"]
        assert r_info["rule"] == "[Gamma]\nCheck tests."

        # Agents are rejected on all /api/projects* routes
        assert (await client.get("/api/projects")).status_code == 403
        assert (await client.post("/api/projects", json={"name": "Bad"})).status_code == 403
        assert (await client.get(f"/api/projects/{p['id']}")).status_code == 403
        assert (await client.patch(f"/api/projects/{p['id']}", json={"name": "Bad"})).status_code == 403
        assert (await client.delete(f"/api/projects/{p['id']}")).status_code == 403
        assert (await client.put(f"/api/projects/{p['id']}/nodes/{n1.id}")).status_code == 403
        assert (await client.delete(f"/api/projects/{p['id']}/nodes/{n1.id}")).status_code == 403
        assert (await client.put(f"/api/projects/{p['id']}/relations/{rel.id}")).status_code == 403
        assert (await client.delete(f"/api/projects/{p['id']}/relations/{rel.id}")).status_code == 403


# --- 9. Clearing overrides to inherit and effective rule composition ----------

@pytest.mark.asyncio
async def test_project_patch_clear_to_inherit_and_effective_rule(env):
    app, db = env
    db.set_relation_rule("Global safeguard.")
    async with human_client(app) as client:
        # Create project with overrides
        res = await client.post("/api/projects", json={
            "name": "Custom Project",
            "relation_rule": "Project specific rule.",
            "max_auto_hops": 4,
            "max_auto_per_hour": 12,
        })
        assert res.status_code == 201
        p_id = res.json()["id"]

        # Check detail has composed effective_rule: "Global safeguard.\n\n[Custom Project]\nProject specific rule."
        detail = (await client.get(f"/api/projects/{p_id}")).json()
        assert detail["effective_rule"] == "Global safeguard.\n\n[Custom Project]\nProject specific rule."
        assert detail["max_auto_hops"] == 4
        assert detail["max_auto_per_hour"] == 12

        # 1. Patch only description: max_auto_hops, max_auto_per_hour, relation_rule must remain UNCHANGED
        res_desc = await client.patch(f"/api/projects/{p_id}", json={
            "description": "Just updating description",
        })
        assert res_desc.status_code == 200
        p_desc = res_desc.json()
        assert p_desc["description"] == "Just updating description"
        assert p_desc["max_auto_hops"] == 4
        assert p_desc["max_auto_per_hour"] == 12
        assert p_desc["relation_rule"] == "Project specific rule."

        # 2. Patch explicit null for max_auto_hops and max_auto_per_hour: must clear to None (inherit)
        res_clear_limits = await client.patch(f"/api/projects/{p_id}", json={
            "max_auto_hops": None,
            "max_auto_per_hour": None,
        })
        assert res_clear_limits.status_code == 200
        p_cleared = res_clear_limits.json()
        assert p_cleared["max_auto_hops"] is None
        assert p_cleared["max_auto_per_hour"] is None
        # relation_rule was not in payload, so it must still be intact
        assert p_cleared["relation_rule"] == "Project specific rule."

        # Verify in DB and detail
        detail2 = (await client.get(f"/api/projects/{p_id}")).json()
        assert detail2["max_auto_hops"] is None
        assert detail2["max_auto_per_hour"] is None
        assert detail2["effective_max_hops"] == 3  # default global
        assert detail2["effective_max_per_hour"] == 20  # default global

        # 3. Patch relation_rule with empty string: must clear to None (inherit)
        res_clear_rule_empty = await client.patch(f"/api/projects/{p_id}", json={
            "relation_rule": "",
        })
        assert res_clear_rule_empty.status_code == 200
        assert res_clear_rule_empty.json()["relation_rule"] is None

        detail3 = (await client.get(f"/api/projects/{p_id}")).json()
        assert detail3["relation_rule"] is None
        assert detail3["effective_rule"] == "Global safeguard."

        # 4. Set relation_rule again, then clear with explicit null
        await client.patch(f"/api/projects/{p_id}", json={"relation_rule": "Another rule"})
        res_clear_rule_null = await client.patch(f"/api/projects/{p_id}", json={
            "relation_rule": None,
        })
        assert res_clear_rule_null.status_code == 200
        assert res_clear_rule_null.json()["relation_rule"] is None

        detail4 = (await client.get(f"/api/projects/{p_id}")).json()
        assert detail4["relation_rule"] is None
        assert detail4["effective_rule"] == "Global safeguard."

        # 5. When global rule is empty and project rule is empty -> effective_rule is None
        db.set_relation_rule("")
        detail5 = (await client.get(f"/api/projects/{p_id}")).json()
        assert detail5["effective_rule"] is None

        # 6. When global rule is empty and project has a rule -> "[Custom Project]\nSolo project rule"
        await client.patch(f"/api/projects/{p_id}", json={"relation_rule": "Solo project rule"})
        detail6 = (await client.get(f"/api/projects/{p_id}")).json()
        assert detail6["effective_rule"] == "[Custom Project]\nSolo project rule"

