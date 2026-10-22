"""
Which Claude Desktop conversations the app really has loaded: the engine's host id
(CLAUDE_CODE_HOST_SESSION_ID, `local_<uuid>`) is mapped through the app's own record to the Placet session id.
An idle session the app no longer holds is suspended after two consecutive reports; nothing is guessed.
"""
import json
import logging
from unittest.mock import MagicMock

import pytest

from backend.app.config import AgentSpec, PlacetConfig
from backend.app.database import Database
from backend.app.main import create_app
from bridge.bridge_daemon import BridgeDaemon
from bridge.claude_desktop import ClaudeDesktopWake
from bridge.claude_desktop_engines import EngineCounter
from tests.helpers import agent_client
from tests.test_desktop_archived import OTHER, record
from tests.test_desktop_capacity import TICKS, fake_proc, proc  # noqa: F401  (proc is a fixture)
from tests.test_desktop_wake_budget import SID, desktop_session

HOST_A = "local_bbe53ab6-febe-44e7-8fc4-c77a64636737"
HOST_B = "local_0a1b2c3d-1111-4222-8333-444455556666"
SECRET = "sk-ant-oat01-SUPERSECRETTOKEN"


def counter(root):
    return EngineCounter(proc_root=root, clk_tck=TICKS)


def host_env(host):
    return f"CLAUDE_CODE_ENTRYPOINT=claude-desktop\0CLAUDE_CODE_HOST_SESSION_ID={host}\0CLAUDE_CODE_OAUTH_TOKEN={SECRET}"


# --- the adapter --------------------------------------------------------------------------


def test_loaded_host_ids_are_read_deduplicated_and_aged(proc):
    fake_proc(proc, 101, env=host_env(HOST_A))
    fake_proc(proc, 102, env=host_env(HOST_A))                                 # a temporary query, same host id
    fake_proc(proc, 103, env=host_env(HOST_B), age=60.0)
    fake_proc(proc, 104, env=host_env("local_99999999-9999-4999-8999-999999999999"), age=2.0)   # too young
    assert counter(proc).loaded() == {"ids": {HOST_A, HOST_B}, "unknown": 0}


def test_an_engine_without_a_valid_host_id_is_counted_as_unknown(proc):
    fake_proc(proc, 101, env=host_env(HOST_A))
    fake_proc(proc, 102)                                                       # no host id at all
    fake_proc(proc, 103, env="CLAUDE_CODE_ENTRYPOINT=claude-desktop\0CLAUDE_CODE_HOST_SESSION_ID=local_../../etc")
    assert counter(proc).loaded() == {"ids": {HOST_A}, "unknown": 2}


def test_no_other_variable_ever_leaves_the_module(proc, caplog):
    fake_proc(proc, 101, env=host_env(HOST_A))
    with caplog.at_level(logging.DEBUG):
        result = counter(proc).loaded()
        counter(proc).snapshot()
    assert set(result) == {"ids", "unknown"}
    assert SECRET not in repr(result) and SECRET not in caplog.text
    assert all(isinstance(i, str) and i.startswith("local_") and len(i) == 42 for i in result["ids"])


def test_loaded_is_none_when_proc_cannot_tell(tmp_path):
    assert EngineCounter(proc_root=tmp_path / "missing", clk_tck=TICKS).loaded() is None


# --- the app's records --------------------------------------------------------------------


def test_the_host_id_maps_to_the_placet_session_through_the_record(tmp_path):
    root = tmp_path / "sessions"
    record(root, HOST_A, SID, False)
    record(root, HOST_B, OTHER, True)
    (root / "account" / "org" / "local_bad.json").write_text("{nope")
    (root / "account" / "org" / "local_link.json").symlink_to(root / "account" / "org" / f"{HOST_A}.json")
    assert ClaudeDesktopWake(root, tmp_path / "state").local_to_cli() == {HOST_A: SID, HOST_B: OTHER}
    assert ClaudeDesktopWake(tmp_path / "missing", tmp_path / "state").local_to_cli() == {}


# --- the bridge ---------------------------------------------------------------------------


def daemon(loaded, mapping):
    waker = MagicMock()
    waker.archived_states.return_value = {}
    waker.local_to_cli.return_value = mapping
    stub = MagicMock()
    stub.snapshot.return_value = None
    stub.loaded.return_value = loaded
    store = MagicMock()
    bridge = BridgeDaemon(store=store, tmux=MagicMock(), channels={}, claude_waker=waker, engine_counter=stub)
    store.reset_mock()
    return bridge, store


def test_the_loaded_session_ids_are_reported_with_the_state():
    bridge, store = daemon({"ids": {HOST_A, HOST_B}, "unknown": 0}, {HOST_A: SID, HOST_B: OTHER})
    bridge.refresh_desktop_state()
    assert store.report_desktop_state.call_args.kwargs["loaded"] == sorted([SID, OTHER])


@pytest.mark.parametrize("loaded, mapping", [
    ({"ids": {HOST_A}, "unknown": 1}, {HOST_A: SID}),                          # an engine with no host id
    ({"ids": {HOST_A, HOST_B}, "unknown": 0}, {HOST_A: SID}),                  # a host id with no record
    (None, {HOST_A: SID}),                                                     # /proc cannot tell
])
def test_when_the_mapping_fails_nothing_is_claimed(loaded, mapping):
    bridge, store = daemon(loaded, mapping)
    bridge.refresh_desktop_state()
    assert store.report_desktop_state.call_args.kwargs["loaded"] is None


def test_a_broken_mapping_never_breaks_the_bridge():
    bridge, store = daemon({"ids": {HOST_A}, "unknown": 0}, None)
    bridge.claude_waker.local_to_cli.side_effect = RuntimeError("boom")
    bridge.refresh_desktop_state()
    assert store.report_desktop_state.call_args.kwargs["loaded"] is None


# --- the backend --------------------------------------------------------------------------


@pytest.fixture
def env(tmp_path):
    db = Database(str(tmp_path / "placet.db"))
    db.init_db()
    config = PlacetConfig(agents={"claude": AgentSpec(tmux_session="agent-claude", token="tok-claude", allowed_targets=["human"])})
    return create_app(db=db, config=config, bridge_token="tok-bridge"), db


async def post_loaded(app, loaded):
    async with agent_client(app, "tok-bridge") as bridge:
        return await bridge.post("/api/bridge/desktop-state", json={"archived": [], "not_archived": [], "loaded": loaded})


@pytest.mark.asyncio
async def test_an_idle_session_the_app_does_not_hold_is_suspended_after_two_reports(env):
    app, db = env
    await desktop_session(app)
    assert (await post_loaded(app, [])).json()["suspended"] == []
    assert db.get_session(SID).suspended_at is None                           # one report is not enough
    assert (await post_loaded(app, [])).json()["suspended"] == [SID]
    assert db.get_session(SID).suspended_at is not None


@pytest.mark.asyncio
async def test_a_loaded_report_in_between_resets_the_count(env):
    app, db = env
    await desktop_session(app)
    await post_loaded(app, [])
    await post_loaded(app, [SID])
    await post_loaded(app, [])
    assert db.get_session(SID).suspended_at is None


@pytest.mark.asyncio
async def test_no_claim_means_no_count(env):
    app, db = env
    await desktop_session(app)
    for _ in range(3):
        assert (await post_loaded(app, None)).status_code == 200
    assert db.get_session(SID).suspended_at is None


@pytest.mark.asyncio
async def test_busy_archived_ended_and_other_sessions_are_never_suspended(env):
    app, db = env
    busy, ended, cli = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222", "33333333-3333-4333-8333-333333333333"
    for sid in (SID, busy, ended):
        await desktop_session(app, sid)
    await desktop_session(app, cli, entrypoint="cli")
    db.set_session_state(busy, "busy")
    db.end_session(ended)
    db.set_desktop_archived([SID], [])
    for _ in range(3):
        await post_loaded(app, [])
    for sid in (SID, busy, cli):
        assert db.get_session(sid).suspended_at is None
    assert db.get_session(ended).suspended_at is None


@pytest.mark.asyncio
async def test_a_session_with_a_live_waiter_is_not_suspended(env):
    app, db = env
    await desktop_session(app)
    db.touch_waiter(SID)
    for _ in range(3):
        await post_loaded(app, [])
    assert db.get_session(SID).suspended_at is None


@pytest.mark.asyncio
async def test_seen_loaded_again_clears_only_the_suspension_made_here(env):
    app, db = env
    await desktop_session(app)
    await post_loaded(app, [])
    await post_loaded(app, [])
    assert db.get_session(SID).suspended_at is not None
    await post_loaded(app, [SID])
    assert db.get_session(SID).suspended_at is None
    db.suspend_session(SID)                                                    # a SessionEnd of the app, not ours
    await post_loaded(app, [SID])
    assert db.get_session(SID).suspended_at is not None


@pytest.mark.asyncio
async def test_a_hook_event_clears_it_and_the_count(env):
    app, db = env
    await desktop_session(app)
    await post_loaded(app, [])
    await post_loaded(app, [])
    await desktop_session(app)                                                 # a new start event
    assert db.get_session(SID).suspended_at is None
    await post_loaded(app, [])
    assert db.get_session(SID).suspended_at is None                            # the count started over


@pytest.mark.asyncio
async def test_too_many_or_odd_loaded_ids_are_refused(env):
    app, db = env
    assert (await post_loaded(app, ["x"] * 2001)).status_code == 422
