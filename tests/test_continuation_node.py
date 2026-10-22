"""
RF-29d, node transfer: after a context continuation the node, its relations
and its project follow the conversation to the new session, as placet_list_relations
(the API behind it) sees them.
"""
import json
import os
import tempfile

import pytest

from backend.app.config import AgentSpec, PlacetConfig
from backend.app.database import Database
from backend.app.main import create_app
from hooks.claude_code import build_event
from tests.helpers import agent_client

CONFIG = PlacetConfig(agents={
    "claude": AgentSpec(tmux_session="agent-claude", token="tok-claude", allowed_targets=["human", "codex"]),
    "codex": AgentSpec(tmux_session="agent-codex", token="tok-codex", allowed_targets=["human", "claude"]),
})
CWD = "/home/paolo/Projects/placet"
OLD = "483d00d3-8a5a-452a-a303-281448994398"
NEW = "9254eb8a-6e7c-4566-bd1a-b0d0280304fe"

SUMMARY = (
    "This session is being continued from a previous conversation that ran out of context. "
    "The summary below covers the earlier portion of the conversation.\n\nSummary:\n1. Primary Request...\n\n"
    f"If you need specific details from before compaction, read the full transcript at: "
    f"/home/paolo/.claude/projects/-home-paolo-Projects-placet/{OLD}.jsonl\n"
)


@pytest.fixture
def env():
    db = Database(os.path.join(tempfile.mkdtemp(), "placet.db"))
    db.init_db()
    return create_app(db=db, config=CONFIG), db


async def post(app, token, session_id, body):
    async with agent_client(app, token) as client:
        resp = await client.post(f"/api/sessions/{session_id}/events", json=body)
        assert resp.status_code == 200, resp.text
        return resp.json()


async def old_session_with_node(app, db):
    await post(app, "tok-claude", OLD, build_event({"hook_event_name": "SessionStart", "cwd": CWD, "source": "startup"}))
    await post(app, "tok-claude", OLD, build_event({"hook_event_name": "UserPromptSubmit", "cwd": CWD, "prompt": "ciao"}))
    await post(app, "tok-claude", OLD, build_event({"hook_event_name": "Stop", "cwd": CWD}))
    await post(app, "tok-codex", "cx", {"event": "start", "provider": "codex", "cwd": CWD})
    planner = db.create_node("planner", OLD)
    builder = db.create_node("builder", "cx")
    review = db.create_relation_type("review", "Ask for a code review")
    db.create_relation(planner.id, builder.id, review.id, "automatic")
    project = db.create_project("Placet")
    db.add_node_to_project(project.id, planner.id)
    return planner, builder, project


async def relations_of(app, session_id):
    async with agent_client(app, "tok-claude") as client:
        return (await client.get("/api/agents/me/relations", params={"session_id": session_id})).json()


@pytest.mark.asyncio
async def test_the_new_session_gets_the_node_and_its_relations(env):
    app, db = env
    planner, builder, project = await old_session_with_node(app, db)
    await post(app, "tok-claude", NEW, build_event({"hook_event_name": "SessionStart", "cwd": CWD, "source": "startup"}))
    first = build_event({"hook_event_name": "UserPromptSubmit", "cwd": CWD, "prompt": SUMMARY})
    assert first["continues"] == OLD
    out = await post(app, "tok-claude", NEW, first)
    assert out["continued_from"] == OLD
    new = await relations_of(app, NEW)
    assert new["node"] == "planner" and [r["to"] for r in new["relations"]] == ["builder"]
    old = await relations_of(app, OLD)
    assert old["node"] is None and old["relations"] == []
    assert db.get_node(planner.id).session_id == NEW
    detail = db.get_project(project.id)  # the project follows the node, hence the new session
    assert [(n.name, n.session_id) for n in detail.nodes] == [("planner", NEW)]


@pytest.mark.asyncio
async def test_late_events_of_the_old_session_do_not_take_the_node_back(env):
    app, db = env
    planner, _, _ = await old_session_with_node(app, db)
    await post(app, "tok-claude", NEW, build_event({"hook_event_name": "SessionStart", "cwd": CWD, "source": "startup"}))
    await post(app, "tok-claude", NEW, build_event({"hook_event_name": "UserPromptSubmit", "cwd": CWD, "prompt": SUMMARY}))
    for name in ("Stop", "SessionEnd", "SessionStart"):
        await post(app, "tok-claude", OLD, build_event({"hook_event_name": name, "cwd": CWD, "source": "startup"}))
    assert db.get_node(planner.id).session_id == NEW
    assert (await relations_of(app, NEW))["node"] == "planner"


@pytest.mark.asyncio
async def test_a_continuation_after_the_end_of_the_old_session_still_transfers(env):
    app, db = env
    planner, _, _ = await old_session_with_node(app, db)
    await post(app, "tok-claude", OLD, build_event({"hook_event_name": "SessionEnd", "cwd": CWD}))
    await post(app, "tok-claude", NEW, build_event({"hook_event_name": "UserPromptSubmit", "cwd": CWD, "prompt": SUMMARY}))
    assert db.get_node(planner.id).session_id == NEW
