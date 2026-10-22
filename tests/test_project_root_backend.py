"""The backend keeps the raw cwd and the project root apart; routing and Home use the root."""
import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.app.config import AgentSpec, PlacetConfig
from backend.app.database import Database
from backend.app.main import create_app
from bridge.claude_desktop import ClaudeDesktopWake
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


async def start(app, agent, sid, provider, cwd, root=None):
    body = {"event": "start", "provider": provider, "cwd": cwd}
    if root:
        body["project_root"] = root
    async with agent_client(app, f"tok-{agent}") as client:
        assert (await client.post(f"/api/sessions/{sid}/events", json=body)).status_code == 200


async def send(app, **body):
    async with agent_client(app, "tok-claude") as client:
        return await client.post("/api/requests", json={"recipient_id": "codex", "prompt": "do it", **body})


@pytest.mark.asyncio
async def test_both_are_stored_and_the_raw_cwd_is_never_replaced(env):
    app, db = env
    await start(app, "claude", "d", "claude-code", "/r/.claude/worktrees/x", "/r")
    await start(app, "claude", "d", "claude-code", "/r/.claude/worktrees/x")   # a later event without a root
    session = db.get_session("d")
    assert (session.cwd, session.project_root) == ("/r/.claude/worktrees/x", "/r")
    async with human_client(app) as human:
        listed = {s["id"]: s for s in (await human.get("/api/sessions")).json()}
    assert listed["d"]["project_root"] == "/r" and listed["d"]["cwd"] == "/r/.claude/worktrees/x"


@pytest.mark.asyncio
async def test_routing_compares_project_roots_not_working_directories(env):
    app, db = env
    await start(app, "claude", "sender", "claude-code", "/r/.claude/worktrees/x", "/r")
    await start(app, "codex", "mine", "codex", "/r/.worktrees/rf29d", "/r")
    await start(app, "codex", "foreign", "codex", "/elsewhere", "/elsewhere")
    resp = await send(app, sender_session_id="sender")
    assert resp.status_code == 201 and resp.json()["recipient_session_id"] == "mine"


@pytest.mark.asyncio
async def test_sessions_without_a_root_still_compare_by_cwd(env):
    app, db = env
    await start(app, "claude", "sender", "claude-code", "/r")
    await start(app, "codex", "mine", "codex", "/r")
    await start(app, "codex", "foreign", "codex", "/elsewhere")
    assert (await send(app, sender_session_id="sender")).json()["recipient_session_id"] == "mine"


@pytest.mark.asyncio
async def test_routing_normalizes_symlinks_on_both_sides(env, tmp_path):
    app, db = env
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    await start(app, "claude", "sender", "claude-code", str(link), str(link))
    await start(app, "codex", "mine", "codex", str(real), str(real))
    await start(app, "codex", "foreign", "codex", "/elsewhere", "/elsewhere")
    assert (await send(app, sender_session_id="sender")).json()["recipient_session_id"] == "mine"


def test_the_desktop_wake_up_still_matches_the_worktree_cwd(tmp_path):
    """The Desktop app records `<repo>/.claude/worktrees/<name>` as the session's cwd."""
    sid, local = "a2c3ef2a-6d99-4567-9909-9fc96489ebf7", "local_bbe53ab6-febe-44e7-8fc4-c77a64636737"
    worktree = tmp_path / "repo" / ".claude" / "worktrees" / "x"
    meta = tmp_path / "sessions" / "account" / "org" / (local + ".json")
    meta.parent.mkdir(parents=True)
    meta.write_text(json.dumps({"sessionId": local, "cliSessionId": sid, "cwd": str(worktree), "isArchived": False}))
    run = MagicMock(return_value=SimpleNamespace(returncode=0))
    wake = ClaudeDesktopWake(tmp_path / "sessions", tmp_path / "state", run, clock=lambda: 1000)
    assert wake.open(sid, str(worktree))
    with pytest.raises(ValueError):
        ClaudeDesktopWake(tmp_path / "sessions", tmp_path / "state2", run, clock=lambda: 1000).open(sid, str(tmp_path / "repo"))


@pytest.mark.asyncio
async def test_the_wake_list_carries_the_raw_cwd(env):
    app, db = env
    await start(app, "claude", "a2c3ef2a-6d99-4567-9909-9fc96489ebf7", "claude-code", "/r/.claude/worktrees/x", "/r")
    [wake] = [s for s in db.list_sessions() if s.id.startswith("a2c3ef2a")]
    assert wake.cwd == "/r/.claude/worktrees/x"
