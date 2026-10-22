"""
RF-62: which remote controls (/compact, /effort) a session offers, from its
provider and how it runs. Only a Claude Code CLI inside tmux can receive the
genuine command; the desktop app stays "at the PC"; Codex and Antigravity
have no verified way.
"""
import pytest

from backend.app.config import AgentSpec, PlacetConfig
from backend.app.database import Database
from backend.app.main import create_app
from tests.helpers import agent_client, human_client

CONFIG = PlacetConfig(agents={
    "claude": AgentSpec(tmux_session="agent-claude", token="tok-claude", allowed_targets=["human"]),
    "codex": AgentSpec(tmux_session="agent-codex", token="tok-codex", allowed_targets=["human"]),
    "gemini": AgentSpec(tmux_session="agent-gemini", token="tok-gemini", allowed_targets=["human"]),
})


@pytest.fixture
def env(tmp_path):
    db = Database(str(tmp_path / "placet.db"))
    db.init_db()
    return create_app(db=db, config=CONFIG, bridge_token="tok-bridge"), db


async def start(app, agent, session_id, provider, entrypoint=None, tmux=None, pane=None, identity=True):
    body = {"event": "start", "provider": provider, "cwd": "/p"}
    if pane:
        body["tmux_pane"] = pane
        if identity:
            body.update({"tmux_server": "4242", "agent_pid": 777, "agent_started": "123456"})
    if entrypoint:
        body["entrypoint"] = entrypoint
    if tmux:
        body["tmux_session"] = tmux
    async with agent_client(app, f"tok-{agent}") as client:
        assert (await client.post(f"/api/sessions/{session_id}/events", json=body)).status_code == 200


SUPPORTED = {"compact": "supported", "effort": "supported"}
PC_ONLY = {"compact": "pc_only", "effort": "pc_only"}
UNSUPPORTED = {"compact": "unsupported", "effort": "unsupported"}


@pytest.mark.asyncio
async def test_controls_follow_the_provider_and_how_it_runs(env):
    app, db = env
    await start(app, "claude", "cli-tmux", "claude-code", "cli", "work", "%3")
    await start(app, "claude", "desktop", "claude-code", "claude-desktop")
    await start(app, "claude", "cli-plain", "claude-code", "cli")
    await start(app, "claude", "cli-old-hook", "claude-code", "cli", "work")  # tmux session known, pane not
    await start(app, "claude", "cli-half-hook", "claude-code", "cli", "work", "%4", identity=False)  # pane, no process
    await start(app, "codex", "cx", "codex", "cli", "work2")
    await start(app, "gemini", "agy", "antigravity", "antigravity", "work3")
    controls = {sid: db.get_session(sid).controls for sid in ("cli-tmux", "desktop", "cli-plain", "cli-old-hook", "cli-half-hook", "cx", "agy")}
    assert controls == {"cli-tmux": SUPPORTED, "desktop": PC_ONLY, "cli-plain": PC_ONLY,
                        "cli-old-hook": UNSUPPORTED, "cli-half-hook": UNSUPPORTED, "cx": UNSUPPORTED, "agy": UNSUPPORTED}


@pytest.mark.asyncio
async def test_the_sessions_api_exposes_them(env):
    app, db = env
    await start(app, "claude", "cli-tmux", "claude-code", "cli", "work", "%3")
    async with human_client(app) as human:
        listed = {s["id"]: s for s in (await human.get("/api/sessions")).json()}
    assert listed["cli-tmux"]["controls"] == SUPPORTED
