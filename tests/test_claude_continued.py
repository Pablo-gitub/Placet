"""Tests for continued Claude Code sessions (RF-29d)."""
import os
import tempfile
import pytest

from backend.app.config import AgentSpec, PlacetConfig
from backend.app.database import Database
from backend.app.main import create_app
from hooks.claude_code import build_event
from tests.helpers import agent_client, human_client

CONFIG = PlacetConfig(agents={
    "claude": AgentSpec(tmux_session="agent-claude", token="tok-claude", allowed_targets=["human"]),
})


@pytest.fixture
def setup():
    db = Database(os.path.join(tempfile.mkdtemp(), "placet.db"))
    db.init_db()
    return create_app(db=db, config=CONFIG, bridge_token="tok-bridge"), db


def test_hook_extracts_continues_uuid():
    # Prompt referencing old session jsonl file
    prompt_jsonl = (
        "This session is being continued from a previous conversation at "
        "/home/paolo/.claude/projects/myproj/11111111-2222-3333-4444-555555555555.jsonl"
    )
    event1 = {"hook_event_name": "UserPromptSubmit", "prompt": prompt_jsonl, "cwd": "/home/paolo/repo"}
    payload1 = build_event(event1)
    assert payload1.get("continues") == "11111111-2222-3333-4444-555555555555"

    # Prompt with direct UUID
    prompt_uuid = "This session is being continued from a previous conversation: aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    event2 = {"hook_event_name": "UserPromptSubmit", "prompt": prompt_uuid, "cwd": "/home/paolo/repo"}
    payload2 = build_event(event2)
    assert payload2.get("continues") == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    # Regular prompt
    event3 = {"hook_event_name": "UserPromptSubmit", "prompt": "Please review my code", "cwd": "/home/paolo/repo"}
    payload3 = build_event(event3)
    assert "continues" not in payload3 or payload3.get("continues") is None

    # Mention without valid uuid
    event4 = {"hook_event_name": "UserPromptSubmit", "prompt": "This session is being continued from a previous conversation without uuid", "cwd": "/home/paolo/repo"}
    payload4 = build_event(event4)
    assert "continues" not in payload4 or payload4.get("continues") is None


@pytest.mark.asyncio
async def test_continuation_on_first_prompt_closes_old_and_migrates_pin_and_node(setup):
    app, db = setup
    old_id = "11111111-1111-1111-1111-111111111111"
    new_id = "22222222-2222-2222-2222-222222222222"
    cwd = "/home/paolo/my-project"

    # 1. Old session setup: start, pin, create node
    async with agent_client(app, "tok-claude") as claude:
        await claude.post(f"/api/sessions/{old_id}/events", json={
            "event": "start", "cwd": cwd, "title": "Primo sviluppatore", "provider": "claude-code"
        })
        await claude.post(f"/api/sessions/{old_id}/events", json={"event": "prompt", "cwd": cwd})
        await claude.post(f"/api/sessions/{old_id}/events", json={"event": "stop", "cwd": cwd})

    async with human_client(app) as human:
        await human.put(f"/api/sessions/{old_id}/pin", json={"pinned": True})
        await human.post("/api/nodes", json={"name": "DevNode", "session_id": old_id})

    old_before = db.get_session(old_id)
    assert old_before.pinned is True
    assert old_before.node_name == "DevNode"
    assert old_before.ended_at is None
    assert old_before.continued_to is None

    # 2. New session starts and sends its first prompt with continues
    async with agent_client(app, "tok-claude") as claude:
        await claude.post(f"/api/sessions/{new_id}/events", json={
            "event": "start", "cwd": cwd, "title": "Primo sviluppatore", "provider": "claude-code"
        })
        resp = await claude.post(f"/api/sessions/{new_id}/events", json={
            "event": "prompt", "cwd": cwd, "continues": old_id
        })
        assert resp.status_code == 200

    # 3. Verify old session closed, continued_to set, pin & node transferred
    old_after = db.get_session(old_id)
    assert old_after.ended_at is not None
    assert old_after.continued_to == new_id
    assert old_after.pinned is False
    assert old_after.node_id is None

    new_after = db.get_session(new_id)
    assert new_after.ended_at is None
    assert new_after.pinned is True
    assert new_after.node_name == "DevNode"


@pytest.mark.asyncio
async def test_continuation_ignored_if_not_first_prompt(setup):
    app, db = setup
    old_id = "11111111-1111-1111-1111-111111111111"
    new_id = "22222222-2222-2222-2222-222222222222"
    cwd = "/home/paolo/my-project"

    # Old session setup
    async with agent_client(app, "tok-claude") as claude:
        await claude.post(f"/api/sessions/{old_id}/events", json={
            "event": "start", "cwd": cwd, "title": "Old Session", "provider": "claude-code"
        })
    async with human_client(app) as human:
        await human.put(f"/api/sessions/{old_id}/pin", json={"pinned": True})

    # New session starts and has already executed a first prompt
    async with agent_client(app, "tok-claude") as claude:
        await claude.post(f"/api/sessions/{new_id}/events", json={
            "event": "start", "cwd": cwd, "title": "New Session", "provider": "claude-code"
        })
        await claude.post(f"/api/sessions/{new_id}/events", json={"event": "prompt", "cwd": cwd})
        await claude.post(f"/api/sessions/{new_id}/events", json={"event": "stop", "cwd": cwd})

        # Second prompt tries to continue old session (e.g. user pasted continuation message)
        await claude.post(f"/api/sessions/{new_id}/events", json={
            "event": "prompt", "cwd": cwd, "continues": old_id
        })

    # Old session must NOT be closed, pin not moved
    old_after = db.get_session(old_id)
    assert old_after.ended_at is None
    assert old_after.continued_to is None
    assert old_after.pinned is True

    new_after = db.get_session(new_id)
    assert new_after.pinned is False


@pytest.mark.asyncio
async def test_continuation_ignored_if_old_already_continued(setup):
    app, db = setup
    old_id = "11111111-1111-1111-1111-111111111111"
    first_continued_id = "22222222-2222-2222-2222-222222222222"
    second_continued_id = "33333333-3333-3333-3333-333333333333"
    cwd = "/home/paolo/my-project"

    async with agent_client(app, "tok-claude") as claude:
        await claude.post(f"/api/sessions/{old_id}/events", json={"event": "start", "cwd": cwd, "provider": "claude-code"})
        # First continuation succeeds
        await claude.post(f"/api/sessions/{first_continued_id}/events", json={"event": "start", "cwd": cwd, "provider": "claude-code"})
        await claude.post(f"/api/sessions/{first_continued_id}/events", json={"event": "prompt", "cwd": cwd, "continues": old_id})

        old_mid = db.get_session(old_id)
        assert old_mid.continued_to == first_continued_id

        # Another session tries to continue the same old session
        await claude.post(f"/api/sessions/{second_continued_id}/events", json={"event": "start", "cwd": cwd, "provider": "claude-code"})
        await claude.post(f"/api/sessions/{second_continued_id}/events", json={"event": "prompt", "cwd": cwd, "continues": old_id})

    # old_id continued_to remains first_continued_id
    old_final = db.get_session(old_id)
    assert old_final.continued_to == first_continued_id


@pytest.mark.asyncio
async def test_continuation_ignored_if_different_agent_or_cwd_or_self(setup):
    app, db = setup
    old_id = "11111111-1111-1111-1111-111111111111"
    new_id = "22222222-2222-2222-2222-222222222222"

    async with agent_client(app, "tok-claude") as claude:
        await claude.post(f"/api/sessions/{old_id}/events", json={"event": "start", "cwd": "/path/a", "provider": "claude-code"})

    # Different CWD
    async with agent_client(app, "tok-claude") as claude:
        await claude.post(f"/api/sessions/{new_id}/events", json={"event": "start", "cwd": "/path/b", "provider": "claude-code"})
        await claude.post(f"/api/sessions/{new_id}/events", json={"event": "prompt", "cwd": "/path/b", "continues": old_id})

    assert db.get_session(old_id).continued_to is None
    assert db.get_session(old_id).ended_at is None

    # Self continuation
    async with agent_client(app, "tok-claude") as claude:
        self_id = "44444444-4444-4444-4444-444444444444"
        await claude.post(f"/api/sessions/{self_id}/events", json={"event": "start", "cwd": "/path/a", "provider": "claude-code"})
        await claude.post(f"/api/sessions/{self_id}/events", json={"event": "prompt", "cwd": "/path/a", "continues": self_id})

    assert db.get_session(self_id).continued_to is None
    assert db.get_session(self_id).ended_at is None
