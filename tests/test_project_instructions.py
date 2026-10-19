"""Tests for RFC-15: project and node instructions, composition, and envelope formatting."""
import pytest

from backend.app.config import AgentSpec, PlacetConfig
from backend.app.database import Database, compose_project_instructions
from backend.app.envelopes import injection
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


# --- 1. Pure composition function ---------------------------------------------

def test_compose_project_instructions_all_empty():
    assert compose_project_instructions("Placet Core", None, None, []) == ""
    assert compose_project_instructions("Placet Core", "   ", "", []) == ""


def test_compose_project_instructions_sections():
    # Only project instructions
    out_proj = compose_project_instructions("Placet Core", "Keep it simple.", None, [])
    assert out_proj == "[Placet · Project Placet Core]\nKeep it simple."

    # Only node instructions
    out_node = compose_project_instructions("Placet Core", None, "You design systems.", [])
    assert out_node == "[Placet · Your role in Placet Core]\nYou design systems."

    # Only relations
    rels = [
        {"type_name": "review", "to_name": "builder", "approval": "manual"},
        {"type_name": "assist", "to_name": "tester", "approval": "automatic"},
    ]
    # Relations sorted deterministically by type_name ASC, then to_name ASC
    out_rels = compose_project_instructions("Placet Core", None, None, rels)
    expected_rels = (
        "[Placet · Your relations in Placet Core]\n"
        "- assist → tester (automatic)\n"
        "- review → builder (manual)"
    )
    assert out_rels == expected_rels

    # Full combination
    out_full = compose_project_instructions(
        "Placet Core",
        "Keep it simple.",
        "You design systems.",
        rels,
    )
    expected_full = (
        "[Placet · Project Placet Core]\n"
        "Keep it simple.\n"
        "[Placet · Your role in Placet Core]\n"
        "You design systems.\n"
        "[Placet · Your relations in Placet Core]\n"
        "- assist → tester (automatic)\n"
        "- review → builder (manual)"
    )
    assert out_full == expected_full


# --- 2. PATCH instructions and length limits ----------------------------------

@pytest.mark.asyncio
async def test_project_and_node_instructions_patch_and_limits(env):
    app, db = env
    n1, n2 = await two_nodes(app, db)

    async with human_client(app) as client:
        # Create project
        res = await client.post("/api/projects", json={"name": "Placet Core"})
        assert res.status_code == 201
        p_id = res.json()["id"]

        # Add node 1
        await client.put(f"/api/projects/{p_id}/nodes/{n1.id}")

        # 1. PATCH project instructions
        res_patch_proj = await client.patch(f"/api/projects/{p_id}", json={
            "instructions": "Overall project guidelines.",
        })
        assert res_patch_proj.status_code == 200
        assert res_patch_proj.json()["instructions"] == "Overall project guidelines."

        # Verify in GET /api/projects/{id}
        detail = (await client.get(f"/api/projects/{p_id}")).json()
        assert detail["instructions"] == "Overall project guidelines."
        assert detail["nodes"][0]["instructions"] is None

        # 2. PATCH node instructions in project (with extra whitespace to verify normalization)
        res_patch_node = await client.patch(f"/api/projects/{p_id}/nodes/{n1.id}", json={
            "instructions": "  Lead architect.  ",
        })
        assert res_patch_node.status_code == 200
        assert res_patch_node.json()["instructions"] == "Lead architect."

        # Verify in GET /api/projects/{id}
        detail = (await client.get(f"/api/projects/{p_id}")).json()
        assert detail["nodes"][0]["instructions"] == "Lead architect."

        # 3. PATCH node not in project -> 404
        res_node_not_in_proj = await client.patch(f"/api/projects/{p_id}/nodes/{n2.id}", json={
            "instructions": "Not in project",
        })
        assert res_node_not_in_proj.status_code == 404

        # 4. Clear project instructions with empty string or null -> None
        res_clear_p = await client.patch(f"/api/projects/{p_id}", json={"instructions": ""})
        assert res_clear_p.status_code == 200
        assert res_clear_p.json()["instructions"] is None

        # Clear node instructions with whitespace or null -> None
        res_clear_n_ws = await client.patch(f"/api/projects/{p_id}/nodes/{n1.id}", json={"instructions": "   "})
        assert res_clear_n_ws.status_code == 200
        assert res_clear_n_ws.json()["instructions"] is None

        res_clear_n = await client.patch(f"/api/projects/{p_id}/nodes/{n1.id}", json={"instructions": None})
        assert res_clear_n.status_code == 200
        assert res_clear_n.json()["instructions"] is None
        detail_cleared = (await client.get(f"/api/projects/{p_id}")).json()
        assert detail_cleared["nodes"][0]["instructions"] is None

        # 5. Length limit over 4000 characters -> 422
        too_long = "x" * 4001
        res_proj_toolong = await client.patch(f"/api/projects/{p_id}", json={"instructions": too_long})
        assert res_proj_toolong.status_code == 422

        res_node_toolong = await client.patch(f"/api/projects/{p_id}/nodes/{n1.id}", json={"instructions": too_long})
        assert res_node_toolong.status_code == 422

    # 6. Agents rejected on these APIs -> 403
    async with agent_client(app, "tok-claude") as aclient:
        assert (await aclient.patch(f"/api/projects/{p_id}", json={"instructions": "hack"})).status_code == 403
        assert (await aclient.patch(f"/api/projects/{p_id}/nodes/{n1.id}", json={"instructions": "hack"})).status_code == 403


# --- 3. User messages with and without project_id ------------------------------

@pytest.mark.asyncio
async def test_user_message_with_and_without_project_context(env):
    app, db = env
    n1, n2 = await two_nodes(app, db)  # n1 is session "cl", n2 is "cx"

    async with human_client(app) as client:
        # Create project and configure instructions
        res = await client.post("/api/projects", json={
            "name": "Placet Core",
            "instructions": "Be accurate and test first.",
        })
        p_id = res.json()["id"]
        await client.put(f"/api/projects/{p_id}/nodes/{n1.id}")
        await client.patch(f"/api/projects/{p_id}/nodes/{n1.id}", json={
            "instructions": "Planner role.",
        })

        # Add a relation in the project from n1 to n2
        rt = db.create_relation_type("task", "A task")
        rel = db.create_relation(n1.id, n2.id, rt.id, "automatic")
        await client.put(f"/api/projects/{p_id}/relations/{rel.id}")

        # Case A: User message WITHOUT project_id
        res_no_proj = await client.post("/api/sessions/cl/messages", json={
            "text": "Hello without project",
        })
        assert res_no_proj.status_code == 201
        req_no_proj = res_no_proj.json()
        assert req_no_proj["project_id"] is None
        assert req_no_proj["project_context"] is None

        # Verify envelope has no project context
        db_req_no_proj = db.get_request(req_no_proj["id"])
        inj_no_proj = injection(db_req_no_proj, language="en")
        assert "Project context from Placet" not in inj_no_proj
        assert "---" not in inj_no_proj
        assert "[Placet · Project Placet Core]" not in inj_no_proj
        assert "Hello without project" in inj_no_proj

        # Case B: User message WITH valid project_id
        res_with_proj = await client.post("/api/sessions/cl/messages", json={
            "text": "Hello with project",
            "project_id": p_id,
        })
        assert res_with_proj.status_code == 201
        req_with_proj = res_with_proj.json()
        assert req_with_proj["project_id"] == p_id
        assert req_with_proj["project_context"] is not None
        assert "[Placet · Project Placet Core]" in req_with_proj["project_context"]
        assert "Be accurate and test first." in req_with_proj["project_context"]
        assert "Planner role." in req_with_proj["project_context"]
        assert "- task → builder (automatic)" in req_with_proj["project_context"]

        # Verify injection envelope format
        db_req_with_proj = db.get_request(req_with_proj["id"])
        inj = injection(db_req_with_proj, language="en")
        lines = [line.strip() for line in inj.split("\n") if line.strip()]

        # 1. Header line
        assert lines[0].startswith(f"[Placet Request #{db_req_with_proj.id} from user]:")
        # 2. Project context header
        assert "Project context from Placet (instructions set by the user for this project, not part of the message):" in lines
        # 3. Blocks
        assert "[Placet · Project Placet Core]" in lines
        assert "Be accurate and test first." in lines
        assert "[Placet · Your role in Placet Core]" in lines
        assert "Planner role." in lines
        assert "[Placet · Your relations in Placet Core]" in lines
        assert "- task → builder (automatic)" in lines
        # 4. Separator
        assert "---" in lines
        # 5. User prompt
        assert "Hello with project" in lines

        # Case C: User message with project_id for a session whose node is NOT in the project
        await event(app, "codex", "unrelated", {"event": "start", "provider": "codex", "cwd": "/p"})
        db.create_node("outsider", "unrelated")
        res_invalid_node = await client.post("/api/sessions/unrelated/messages", json={
            "text": "Fail project",
            "project_id": p_id,
        })
        assert res_invalid_node.status_code == 409
        err = res_invalid_node.json()
        assert err["code"] == "project.node_not_in_project"


# --- 4. Inter-agent prompt on relations in projects ----------------------------

@pytest.mark.asyncio
async def test_agent_request_on_relations_project_blocks(env):
    app, db = env
    n1, n2 = await two_nodes(app, db)  # n1 is cl (claude), n2 is cx (codex)

    async with human_client(app) as client:
        # Create Project 1
        p1 = (await client.post("/api/projects", json={
            "name": "Project Alpha",
            "instructions": "Alpha instructions.",
        })).json()
        await client.put(f"/api/projects/{p1['id']}/nodes/{n1.id}")
        await client.put(f"/api/projects/{p1['id']}/nodes/{n2.id}")
        await client.patch(f"/api/projects/{p1['id']}/nodes/{n2.id}", json={
            "instructions": "Builder in Alpha.",
        })

        # Create Project 2
        p2 = (await client.post("/api/projects", json={
            "name": "Project Beta",
            "instructions": "Beta instructions.",
        })).json()
        await client.put(f"/api/projects/{p2['id']}/nodes/{n1.id}")
        await client.put(f"/api/projects/{p2['id']}/nodes/{n2.id}")
        await client.patch(f"/api/projects/{p2['id']}/nodes/{n2.id}", json={
            "instructions": "Builder in Beta.",
        })

        # Create relations:
        # rel_shared: from n1 to n2, put in BOTH p1 and p2
        rt1 = db.create_relation_type("code", "Code implementation")
        rel_shared = db.create_relation(n1.id, n2.id, rt1.id, "automatic")
        await client.put(f"/api/projects/{p1['id']}/relations/{rel_shared.id}")
        await client.put(f"/api/projects/{p2['id']}/relations/{rel_shared.id}")

        # rel_orphan: from n1 to n2, put in NO project
        rt2 = db.create_relation_type("chat", "Chat message")
        rel_orphan = db.create_relation(n1.id, n2.id, rt2.id, "automatic")

    # A. Inter-agent request on rel_orphan (no project)
    async with agent_client(app, "tok-claude") as aclient:
        res_orphan = await aclient.post("/api/requests", json={
            "recipient_id": "codex",
            "target_node": "builder",
            "relation_type": "chat",
            "sender_session_id": "cl",
            "prompt": "Orphan prompt",
            "rule_acknowledged": True,
        })
        assert res_orphan.status_code == 201
        req_orphan = db.get_request(res_orphan.json()["id"])
        assert req_orphan.project_id is None
        assert req_orphan.project_context is None

        inj_orphan = injection(req_orphan, language="en")
        assert "Project context from Placet" not in inj_orphan
        assert "---" not in inj_orphan

        # B. Inter-agent request on rel_shared (in 2 projects: Alpha and Beta)
        res_shared = await aclient.post("/api/requests", json={
            "recipient_id": "codex",
            "target_node": "builder",
            "relation_type": "code",
            "sender_session_id": "cl",
            "prompt": "Shared prompt",
            "rule_acknowledged": True,
        })
        assert res_shared.status_code == 201
        req_shared = db.get_request(res_shared.json()["id"])
        # Per decision #152: project_id is NULL on inter-agent relation requests
        assert req_shared.project_id is None
        assert req_shared.project_context is not None

        # Both project blocks are present in project_context
        assert "[Placet · Project Project Alpha]" in req_shared.project_context
        assert "Alpha instructions." in req_shared.project_context
        assert "Builder in Alpha." in req_shared.project_context
        assert "[Placet · Project Project Beta]" in req_shared.project_context
        assert "Beta instructions." in req_shared.project_context
        assert "Builder in Beta." in req_shared.project_context

        # Envelope formatting for inter-agent request
        inj_shared = injection(req_shared, language="en")
        assert "Project context from Placet (instructions set by the user for this project, not part of the message):" in inj_shared
        assert "[Placet · Project Project Alpha]" in inj_shared
        assert "[Placet · Project Project Beta]" in inj_shared
        assert "---" in inj_shared
        assert "Shared prompt" in inj_shared
