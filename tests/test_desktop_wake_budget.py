"""
Claude Desktop wake-up: one automatic open per episode, persisted per session.
The link being accepted (rc=0) does not mean the session started; asking again
every five minutes forever did nothing but reopen the chat.
"""
import subprocess
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from backend.app.config import AgentSpec, PlacetConfig
from backend.app.database import Database
from backend.app.main import create_app
from bridge.bridge_daemon import BridgeDaemon
from tests.helpers import agent_client, human_client

CONFIG = PlacetConfig(agents={
    "claude": AgentSpec(tmux_session="agent-claude", token="tok-claude", allowed_targets=["human"]),
})
SID = "a2c3ef2a-6d99-4567-9909-9fc96489ebf7"


@pytest.fixture
def env(tmp_path):
    path = str(tmp_path / "placet.db")
    db = Database(path)
    db.init_db()
    return create_app(db=db, config=CONFIG, bridge_token="tok-bridge"), db, path


async def desktop_session(app, sid=SID, entrypoint="claude-desktop"):
    body = {"event": "start", "provider": "claude-code", "entrypoint": entrypoint, "cwd": "/lab"}
    async with agent_client(app, "tok-claude") as claude:
        assert (await claude.post(f"/api/sessions/{sid}/events", json=body)).status_code == 200


async def write(app, text="ciao", sid=SID):
    async with human_client(app) as human:
        return (await human.post(f"/api/sessions/{sid}/messages", json={"text": text})).json()["id"]


async def report(app, outcome, sid=SID):
    async with agent_client(app, "tok-bridge") as bridge:
        return await bridge.post(f"/api/bridge/sessions/{sid}/wake", json={"outcome": outcome})


async def work_ids(app):
    async with agent_client(app, "tok-bridge") as bridge:
        return [s["id"] for s in (await bridge.get("/api/bridge/work")).json()["claude_wake_sessions"]]


@pytest.mark.asyncio
async def test_one_accepted_open_ends_the_automatic_attempts_of_the_episode(env):
    app, db, path = env
    await desktop_session(app)
    await write(app)
    assert await work_ids(app) == [SID]
    assert (await report(app, "open_requested")).status_code == 200
    assert await work_ids(app) == []
    await write(app, "un altro messaggio")                          # an extra message is the same episode
    assert await work_ids(app) == []
    with db._get_connection() as conn:                               # hours later: still no automatic open
        conn.execute("UPDATE sessions SET wake_at = ? WHERE id = ?",
                     ((datetime.now(timezone.utc) - timedelta(hours=5)).isoformat(), SID))
    assert await work_ids(app) == []
    assert db.claude_sessions_to_wake() == []


@pytest.mark.asyncio
async def test_the_budget_survives_a_restart(env):
    app, db, path = env
    await desktop_session(app)
    await write(app)
    await report(app, "open_requested")
    restarted = Database(path)
    restarted.init_db()
    assert restarted.claude_sessions_to_wake() == []


@pytest.mark.asyncio
async def test_a_second_open_only_if_the_first_certainly_failed_before_the_handoff(env):
    app, db, path = env
    await desktop_session(app)
    await write(app)
    await report(app, "failed")
    assert await work_ids(app) == [SID]            # nothing was handed to the app: one more try
    await report(app, "failed")
    assert await work_ids(app) == []               # two failures end the automatic attempts
    assert db.get_session(SID).wake_attempts == 2


@pytest.mark.asyncio
async def test_a_failure_after_an_accepted_open_cannot_reopen_the_episode(env):
    app, db, path = env
    await desktop_session(app)
    await write(app)
    await report(app, "open_requested")
    await report(app, "failed")                     # a late or duplicate report
    assert await work_ids(app) == []


@pytest.mark.asyncio
async def test_only_the_bridge_reports_and_only_for_desktop_sessions(env):
    app, db, path = env
    await desktop_session(app)
    await desktop_session(app, "cli-session", entrypoint="cli")
    async with agent_client(app, "tok-claude") as claude:
        assert (await claude.post(f"/api/bridge/sessions/{SID}/wake", json={"outcome": "failed"})).status_code == 403
    async with human_client(app) as human:
        assert (await human.post(f"/api/bridge/sessions/{SID}/wake", json={"outcome": "failed"})).status_code == 403
    assert (await report(app, "opened")).status_code == 422
    assert (await report(app, "failed", "cli-session")).status_code == 404
    assert (await report(app, "failed", "nobody")).status_code == 404


# --- the bridge ---------------------------------------------------------------------------


def daemon(waker):
    store = MagicMock()
    bridge = BridgeDaemon(store=store, tmux=MagicMock(), channels={}, claude_waker=waker)
    store.reset_mock()                                # what the constructor did is not the question
    return bridge, store


@pytest.mark.parametrize("raised,outcome", [
    (None, "open_requested"),
    (RuntimeError("did not accept"), "failed"),
    (ValueError("mapping missing"), "failed"),
    (FileNotFoundError("claude-desktop"), "failed"),
    (PermissionError("state dir"), "failed"),
    (subprocess.TimeoutExpired("claude-desktop", 20), "open_requested"),   # may have been handed over
])
def test_the_bridge_tells_what_happened_to_the_link(raised, outcome):
    waker = MagicMock()
    if raised is not None:
        waker.open.side_effect = raised
    bridge, store = daemon(waker)
    assert bridge.wake_claude_sessions([{"id": SID, "cwd": "/lab"}]) == {SID: outcome}
    assert not store.mock_calls                      # deciding and doing never touch the store


def test_the_loop_reports_the_outcome_to_the_backend():
    from bridge.stores import Work
    waker = MagicMock()
    bridge, store = daemon(waker)
    store.work.return_value = Work(claude_wake_sessions=[{"id": SID, "cwd": "/lab"}])
    bridge.run_loop_step()
    store.report_wake.assert_called_once_with(SID, "open_requested")
    store.report_wake.side_effect = RuntimeError("backend down")      # a failed report must not stop the loop
    bridge._claude_wake_at.clear()
    bridge.run_loop_step()


def test_a_declined_open_reports_nothing_and_a_forced_one_skips_the_cooldown():
    waker = MagicMock()
    waker.open.return_value = False                 # cooling down, locked, or an excluded folder
    bridge, store = daemon(waker)
    assert bridge.wake_claude_sessions([{"id": SID, "cwd": "/lab"}]) == {}
    waker.reset_mock()
    waker.open.return_value = True
    bridge, store = daemon(waker)
    assert bridge.wake_claude_sessions([{"id": SID, "cwd": "/lab", "force": True}]) == {SID: "open_requested"}
    waker.open.assert_called_once_with(SID, "/lab", force=True)
