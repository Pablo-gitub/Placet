"""
A conversation archived in Claude Desktop: the app stops its engine, so Placet must not keep showing it as
waiting, nor try to wake it. The bridge reads the app's own session records (read only, like resolve()).
"""
import json
from unittest.mock import MagicMock

import pytest

from backend.app.config import AgentSpec, PlacetConfig
from backend.app.database import Database
from backend.app.main import create_app
from bridge.bridge_daemon import BridgeDaemon
from bridge.claude_desktop import ClaudeDesktopWake
from tests.helpers import agent_client, human_client
from tests.test_desktop_wake_budget import CONFIG, SID, desktop_session, write

OTHER = "b3d4f0a1-7e11-4c22-8f33-123456789abc"
LOCAL = "local_bbe53ab6-febe-44e7-8fc4-c77a64636737"


def record(root, name, cli_id, archived, **extra):
    path = root / "account" / "org" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"sessionId": name, "cliSessionId": cli_id, "cwd": "/lab", "isArchived": archived, **extra}))
    return path


# --- the adapter --------------------------------------------------------------------------


def test_the_archived_flag_of_each_known_session_is_read(tmp_path):
    root = tmp_path / "sessions"
    record(root, "local_a", SID, True)
    record(root, "local_b", OTHER, False)
    assert ClaudeDesktopWake(root, tmp_path / "state").archived_states() == {SID: True, OTHER: False}


def test_unsafe_or_odd_records_are_skipped_never_raised(tmp_path):
    root = tmp_path / "sessions"
    good = record(root, "local_a", SID, True)
    link = good.parent / "local_link.json"
    link.symlink_to(good)                                                 # a symlink is never followed
    (good.parent / "local_bad.json").write_text("{not json")
    record(root, "local_notuuid", "not-a-uuid", True)
    record(root, "local_nostr", 12345, True)
    big = good.parent / "local_big.json"
    big.write_text(json.dumps({"cliSessionId": OTHER, "isArchived": True, "pad": "x" * 2_000_000}))
    (good.parent / "local_dir.json").mkdir()
    assert ClaudeDesktopWake(root, tmp_path / "state").archived_states() == {SID: True}
    assert ClaudeDesktopWake(tmp_path / "missing", tmp_path / "state").archived_states() == {}


def test_two_records_for_one_session_are_ambiguous_and_say_nothing(tmp_path):
    root = tmp_path / "sessions"
    record(root, "local_a", SID, True)
    other = root / "account2" / "org"
    other.mkdir(parents=True)
    (other / "local_c.json").write_text(json.dumps({"sessionId": "local_c", "cliSessionId": SID, "isArchived": False}))
    assert ClaudeDesktopWake(root, tmp_path / "state").archived_states() == {}


def test_a_wake_of_an_archived_conversation_is_still_refused(tmp_path):
    root = tmp_path / "sessions"
    record(root, LOCAL, SID, True)
    run = MagicMock()
    with pytest.raises(ValueError):
        ClaudeDesktopWake(root, tmp_path / "state", run).open(SID, "/lab")
    run.assert_not_called()


# --- the bridge ---------------------------------------------------------------------------


def daemon(waker):
    store = MagicMock()
    bridge = BridgeDaemon(store=store, tmux=MagicMock(), channels={}, claude_waker=waker)
    store.reset_mock()
    return bridge, store


def test_the_bridge_reports_archived_and_unarchived_every_minute_only(monkeypatch):
    waker = MagicMock()
    waker.archived_states.return_value = {SID: True, OTHER: False}
    bridge, store = daemon(waker)
    clock = [1000.0]
    monkeypatch.setattr("bridge.bridge_daemon.time.monotonic", lambda: clock[0])
    bridge.refresh_desktop_state()
    store.report_desktop_state.assert_called_once()
    assert store.report_desktop_state.call_args.kwargs["archived"] == [SID]
    assert store.report_desktop_state.call_args.kwargs["not_archived"] == [OTHER]
    clock[0] += 30
    bridge.refresh_desktop_state()
    assert store.report_desktop_state.call_count == 1                    # not before 60 s
    clock[0] += 31
    bridge.refresh_desktop_state()
    assert store.report_desktop_state.call_count == 2


def test_a_broken_adapter_or_backend_never_breaks_the_bridge(monkeypatch):
    waker = MagicMock()
    waker.archived_states.side_effect = RuntimeError("records exploded")
    bridge, store = daemon(waker)
    bridge.refresh_desktop_state()
    waker.archived_states.side_effect = None
    waker.archived_states.return_value = object()                         # not a mapping: ignored
    bridge._desktop_state_at = None
    bridge.refresh_desktop_state()
    store.report_desktop_state.assert_not_called()
    waker.archived_states.return_value = {SID: True}
    store.report_desktop_state.side_effect = RuntimeError("backend down")
    bridge._desktop_state_at = None
    bridge.refresh_desktop_state()


# --- the backend --------------------------------------------------------------------------


@pytest.fixture
def env(tmp_path):
    db = Database(str(tmp_path / "placet.db"))
    db.init_db()
    return create_app(db=db, config=CONFIG, bridge_token="tok-bridge"), db


async def state(app, archived=(), not_archived=(), token="tok-bridge"):
    async with agent_client(app, token) as client:
        return await client.post("/api/bridge/desktop-state", json={"archived": list(archived), "not_archived": list(not_archived)})


@pytest.mark.asyncio
async def test_the_flag_is_set_cleared_and_exposed(env):
    app, db = env
    await desktop_session(app)
    assert db.get_session(SID).desktop_archived is False
    assert (await state(app, archived=[SID])).status_code == 200
    assert db.get_session(SID).desktop_archived is True
    async with human_client(app) as human:
        listed = {s["id"]: s for s in (await human.get("/api/sessions")).json()}
    assert listed[SID]["desktop_archived"] is True
    await state(app, not_archived=[SID])                                  # unarchived in the app: cleared by itself
    assert db.get_session(SID).desktop_archived is False


@pytest.mark.asyncio
async def test_only_the_bridge_reports_and_only_desktop_sessions_are_touched(env):
    app, db = env
    await desktop_session(app)
    await desktop_session(app, "cli-session", entrypoint="cli")
    assert (await state(app, archived=[SID], token="tok-claude")).status_code == 403
    async with human_client(app) as human:
        assert (await human.post("/api/bridge/desktop-state", json={"archived": [SID], "not_archived": []})).status_code == 403
    await state(app, archived=["cli-session", "nobody"])
    assert db.get_session("cli-session").desktop_archived is False
    big = await state(app, archived=[f"s{i}" for i in range(2001)])
    assert big.status_code == 422                                         # bounded


@pytest.mark.asyncio
async def test_an_archived_session_is_never_woken_and_cannot_be_retried(env):
    app, db = env
    await desktop_session(app)
    await write(app)
    async with agent_client(app, "tok-bridge") as bridge:
        assert [s["id"] for s in (await bridge.get("/api/bridge/work")).json()["claude_wake_sessions"]] == [SID]
    await state(app, archived=[SID])
    async with agent_client(app, "tok-bridge") as bridge:
        assert (await bridge.get("/api/bridge/work")).json()["claude_wake_sessions"] == []
    async with human_client(app) as human:
        resp = await human.post(f"/api/sessions/{SID}/wake")
    assert resp.status_code == 409 and resp.json()["code"] == "session.desktop_archived"
    await state(app, not_archived=[SID])
    async with agent_client(app, "tok-bridge") as bridge:
        assert [s["id"] for s in (await bridge.get("/api/bridge/work")).json()["claude_wake_sessions"]] == [SID]
