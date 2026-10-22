"""
RF-62: /compact and /effort typed into a Claude Code CLI's tmux pane through
a bridge job. Fixed command strings only, never text from the phone.
"""
import asyncio

import pytest

from backend.app.config import AgentSpec, PlacetConfig
from backend.app.database import Database
from backend.app.main import create_app
from backend.app.session_commands import command_text
from bridge.bridge_daemon import BridgeDaemon
from tests.helpers import agent_client, human_client

CONFIG = PlacetConfig(agents={
    "claude": AgentSpec(tmux_session="agent-claude", token="tok-claude", allowed_targets=["human"],
                        idle_patterns=["❯"], busy_patterns=["esc to interrupt"]),
    "codex": AgentSpec(tmux_session="agent-codex", token="tok-codex", allowed_targets=["human"]),
})


@pytest.fixture
def env(tmp_path):
    db = Database(str(tmp_path / "placet.db"))
    db.init_db()
    return create_app(db=db, config=CONFIG, bridge_token="tok-bridge"), db


async def start(app, agent, session_id, provider, entrypoint="cli", tmux="agent-claude"):
    body = {"event": "start", "provider": provider, "cwd": "/p", "entrypoint": entrypoint}
    if tmux:
        body["tmux_session"] = tmux
    async with agent_client(app, f"tok-{agent}") as client:
        assert (await client.post(f"/api/sessions/{session_id}/events", json=body)).status_code == 200


async def fake_bridge(app, handle, polls=3):
    async with agent_client(app, "tok-bridge") as bridge:
        for _ in range(polls):
            resp = await bridge.get("/api/bridge/jobs?wait_seconds=5")
            if resp.status_code == 204:
                continue
            for job in resp.json()["jobs"]:
                await bridge.post(f"/api/bridge/jobs/{job['id']}/result", json=handle(job))


async def post(app, path, body=None, bridge_answer=None, seen=None):
    """A human POST while a fake bridge answers the job, if one is given."""
    task = None
    if bridge_answer is not None:
        def handle(job):
            if seen is not None:
                seen.append(job)
            return bridge_answer
        task = asyncio.create_task(fake_bridge(app, handle, polls=1))
        await asyncio.sleep(0.05)
    async with human_client(app) as human:
        resp = await human.post(path, json=body) if body is not None else await human.post(path)
    if task:
        await task
    return resp


def test_the_command_strings_are_fixed():
    assert command_text("compact") == "/compact"
    assert command_text("effort", "max") == "/effort max"
    for bad in (("effort", None), ("effort", "extreme"), ("effort", "high; rm -rf"), ("rm", None), ("compact", "x\n")):
        with pytest.raises(ValueError):
            command_text(*bad)


@pytest.mark.asyncio
async def test_compact_goes_to_the_bridge_as_a_fixed_command(env):
    app, db = env
    await start(app, "claude", "s1", "claude-code")
    seen = []
    resp = await post(app, "/api/sessions/s1/compact", bridge_answer={"ok": True, "page": {"sent": "/compact"}}, seen=seen)
    assert resp.status_code == 200 and resp.json()["command"] == "/compact"
    job = seen[0]
    assert job["kind"] == "session_command" and job["command"] == "compact"
    assert job["tmux_session"] == "agent-claude" and job["agent_id"] == "claude"
    assert set(job) <= {"id", "kind", "command", "level", "tmux_session", "agent_id"}  # no free text
    [event] = db.list_session_controls("s1")
    assert event["actor"].startswith("human") and event["command"] == "/compact"


@pytest.mark.asyncio
async def test_effort_takes_a_level_from_the_allowlist(env):
    app, db = env
    await start(app, "claude", "s1", "claude-code")
    seen = []
    resp = await post(app, "/api/sessions/s1/effort", {"level": "xhigh"},
                      bridge_answer={"ok": True, "page": {"sent": "/effort xhigh"}}, seen=seen)
    assert resp.status_code == 200 and resp.json()["command"] == "/effort xhigh"
    assert seen[0]["level"] == "xhigh"
    for level in ("extreme", "", "high\n/compact", "HIGH"):
        bad = await post(app, "/api/sessions/s1/effort", {"level": level})
        assert bad.status_code == 422 and bad.json()["code"] == "session.effort_invalid"
    assert len(db.list_session_controls("s1")) == 1


@pytest.mark.asyncio
async def test_only_a_supported_idle_open_session_takes_a_command(env):
    app, db = env
    await start(app, "claude", "desktop", "claude-code", entrypoint="claude-desktop", tmux=None)
    await start(app, "codex", "cx", "codex")
    await start(app, "claude", "busy", "claude-code")
    db.set_session_state("busy", "busy", None)
    await start(app, "claude", "gone", "claude-code")
    db.end_session("gone")
    expected = {"desktop": (409, "session.control_pc_only"), "cx": (409, "session.control_unsupported"),
                "busy": (409, "session.busy"), "gone": (409, "session.closed"), "nope": (404, "session.not_found")}
    for sid, (code, product) in expected.items():
        for path, body in ((f"/api/sessions/{sid}/compact", None), (f"/api/sessions/{sid}/effort", {"level": "low"})):
            resp = await post(app, path, body)
            assert (resp.status_code, resp.json()["code"]) == (code, product), (sid, path)
    assert db.list_session_controls("busy") == []


@pytest.mark.asyncio
async def test_only_the_human_may_ask(env):
    app, db = env
    await start(app, "claude", "s1", "claude-code")
    async with agent_client(app, "tok-claude") as claude:
        assert (await claude.post("/api/sessions/s1/compact")).status_code == 403
        assert (await claude.post("/api/sessions/s1/effort", json={"level": "low"})).status_code == 403
    async with agent_client(app, "tok-bridge") as bridge:
        assert (await bridge.post("/api/sessions/s1/compact")).status_code == 403


@pytest.mark.asyncio
async def test_bridge_failures_become_product_errors(env):
    app, db = env
    await start(app, "claude", "s1", "claude-code")
    resp = await post(app, "/api/sessions/s1/compact")  # nobody polls: the bridge is offline
    assert resp.status_code == 503 and resp.json()["code"] == "bridge.offline"
    resp = await post(app, "/api/sessions/s1/compact", bridge_answer={"ok": False, "error": "not_idle"})
    assert resp.status_code == 409 and resp.json()["code"] == "session_command.not_idle"
    assert db.list_session_controls("s1") == []  # nothing was sent, nothing audited


class FakeTmux:
    def __init__(self, exists=True, idle=True, typing=None):
        self.exists, self.idle, self.typing, self.sent = exists, idle, typing, []

    def session_exists(self, name):
        return self.exists

    def is_session_idle(self, name, idle_patterns, busy_patterns):
        return self.idle

    def seconds_since_input(self, name):
        return self.typing

    def send_keys(self, name, text, enter=True):
        self.sent.append((name, text, enter))
        return True


def daemon(env, tmux):
    app, db = env
    return BridgeDaemon(db=db, config=CONFIG, tmux=tmux)


JOB = {"id": "j", "kind": "session_command", "agent_id": "claude", "tmux_session": "agent-claude", "command": "compact"}


def test_the_bridge_types_only_the_fixed_command_into_the_configured_pane(env):
    tmux = FakeTmux()
    out = daemon(env, tmux).handle_job({**JOB, "text": "rm -rf /"})
    assert out == {"ok": True, "page": {"sent": "/compact"}}
    assert tmux.sent == [("agent-claude", "/compact", True)]
    tmux = FakeTmux()
    daemon(env, tmux).handle_job({**JOB, "command": "effort", "level": "max"})
    assert tmux.sent == [("agent-claude", "/effort max", True)]


@pytest.mark.parametrize("job,tmux,error", [
    ({"tmux_session": "somewhere-else"}, FakeTmux(), "wrong_session"),
    ({"agent_id": "nobody"}, FakeTmux(), "wrong_session"),
    ({}, FakeTmux(exists=False), "tmux_missing"),
    ({}, FakeTmux(idle=False), "not_idle"),
    ({}, FakeTmux(typing=3.0), "user_typing"),
    ({"command": "model"}, FakeTmux(), "invalid"),
    ({"command": "effort", "level": "extreme"}, FakeTmux(), "invalid"),
])
def test_the_bridge_refuses_what_is_not_safe(env, job, tmux, error):
    out = daemon(env, tmux).handle_job({**JOB, **job})
    assert out["ok"] is False and out["error"] == error
    assert tmux.sent == []
