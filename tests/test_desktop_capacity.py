"""
How many Claude Desktop engines are loaded against the app's cap, counted from /proc (Linux), numbers only.
The app also spawns short-lived "temporary query" engines several times a minute: they must not count.
"""
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from backend.app.config import AgentSpec, PlacetConfig
from backend.app.database import Database
from backend.app.main import create_app
from bridge.bridge_daemon import BridgeDaemon
from bridge.claude_desktop_engines import EngineCounter
from tests.helpers import agent_client, human_client

TICKS = 100          # clock ticks per second in the fake /proc
UPTIME = 5000.0
ENGINE = ["/opt/Claude/resources/app/claude-code/2.1.303/claude", "--output-format", "stream-json", "--verbose"]


def fake_proc(root, pid, argv=None, env="CLAUDE_CODE_ENTRYPOINT=claude-desktop", age=60.0, uid=None):
    folder = root / str(pid)
    folder.mkdir(parents=True)
    (folder / "cmdline").write_bytes(b"\0".join(a.encode() for a in (argv or ENGINE)) + b"\0")
    (folder / "environ").write_bytes(b"\0".join(e.encode() for e in ["HOME=/home/u", env, "LANG=C"] if e) + b"\0")
    start = int((UPTIME - age) * TICKS)
    fields = ["S", "1"] + ["0"] * 17 + [str(start)] + ["0"] * 10
    (folder / "stat").write_text(f"{pid} (claude) " + " ".join(fields) + "\n")
    return folder


@pytest.fixture
def proc(tmp_path):
    root = tmp_path / "proc"
    root.mkdir()
    (root / "uptime").write_text(f"{UPTIME} 1000.0\n")
    (root / "meminfo").write_text("MemTotal:       16384000 kB\nMemFree: 1 kB\n")
    return root


def counter(root):
    return EngineCounter(proc_root=root, clk_tck=TICKS)


def test_only_long_lived_desktop_engines_count(proc):
    fake_proc(proc, 101)                                                    # an engine of the app, up for a minute
    fake_proc(proc, 102, age=300)
    fake_proc(proc, 103, age=3)                                             # a short-lived temporary query
    fake_proc(proc, 104, env="CLAUDE_CODE_ENTRYPOINT=cli")                  # a terminal session, not the app
    fake_proc(proc, 105, env="")                                            # no entrypoint at all
    fake_proc(proc, 106, argv=["/usr/bin/claude", "--output-format", "text"])
    fake_proc(proc, 107, argv=["/usr/bin/node", "server.js"])
    fake_proc(proc, 108, argv=["/opt/Claude/claude", "--output-format=stream-json"])    # the other spelling
    fake_proc(proc, 109, argv=["/usr/bin/python3", "--output-format", "stream-json"])   # not the engine
    (proc / "self").mkdir()
    (proc / "cpuinfo").write_text("x")
    assert counter(proc).count() == 3
    assert counter(proc).count(min_age=1.0) == 4


def test_the_cap_follows_the_apps_formula(proc):
    cases = {"MemTotal:  4194304 kB\n": 6,          # 4 GiB -> floor(4/3)=1 -> at least 6
             "MemTotal: 16777216 kB\n": 6,          # 16 GiB -> 5 -> 6
             "MemTotal: 25165824 kB\n": 8,          # 24 GiB -> 8
             "MemTotal: 33554432 kB\n": 10}         # 32 GiB -> 10
    for meminfo, cap in cases.items():
        (proc / "meminfo").write_text(meminfo)
        assert counter(proc).cap() == cap


def test_processes_of_other_users_and_broken_ones_are_skipped_never_raised(proc, monkeypatch):
    fake_proc(proc, 101)
    other = fake_proc(proc, 102)
    broken = fake_proc(proc, 103)
    (broken / "stat").write_text("garbage")
    (proc / "104").mkdir()                                                   # vanished: nothing inside
    unreadable = fake_proc(proc, 105)
    (unreadable / "environ").unlink()
    assert counter(proc).count() == 2                                        # 101 and 102; the broken ones skipped
    real = os.stat

    def stat(path, *a, **k):
        info = real(path, *a, **k)
        if str(path).endswith("/102"):
            return os.stat_result((info.st_mode, info.st_ino, info.st_dev, info.st_nlink, os.getuid() + 1, *info[5:]))
        return info
    monkeypatch.setattr("bridge.claude_desktop_engines.os.stat", stat)
    assert counter(proc).count() == 1                                        # 102 belongs to someone else now


def test_no_proc_means_no_numbers(tmp_path):
    assert EngineCounter(proc_root=tmp_path / "missing", clk_tck=TICKS).snapshot() is None
    empty = tmp_path / "proc"
    empty.mkdir()
    assert EngineCounter(proc_root=empty, clk_tck=TICKS).snapshot() is None  # no uptime/meminfo: cannot tell


def test_the_snapshot_is_numbers_only(proc):
    fake_proc(proc, 101)
    assert counter(proc).snapshot() == {"engines": 1, "cap": 6}


def test_a_huge_environment_is_read_boundedly(proc):
    folder = fake_proc(proc, 101)
    (folder / "environ").write_bytes(b"A=" + b"x" * 5_000_000 + b"\0CLAUDE_CODE_ENTRYPOINT=claude-desktop\0")
    assert counter(proc).count() == 0                                        # beyond the bound: not claimed


# --- the bridge ---------------------------------------------------------------------------


def daemon(counter_stub):
    waker = MagicMock()
    waker.archived_states.return_value = {}
    store = MagicMock()
    bridge = BridgeDaemon(store=store, tmux=MagicMock(), channels={}, claude_waker=waker, engine_counter=counter_stub)
    store.reset_mock()
    return bridge, store


def test_the_bridge_reports_the_numbers_with_the_state(monkeypatch):
    stub = MagicMock()
    stub.snapshot.return_value = {"engines": 6, "cap": 7}
    bridge, store = daemon(stub)
    bridge.refresh_desktop_state()
    assert store.report_desktop_state.call_args.kwargs["capacity"] == {"engines": 6, "cap": 7}


def test_a_broken_counter_or_none_reports_no_numbers_and_never_breaks():
    stub = MagicMock()
    stub.snapshot.side_effect = RuntimeError("proc exploded")
    bridge, store = daemon(stub)
    bridge.refresh_desktop_state()
    assert store.report_desktop_state.call_args.kwargs["capacity"] is None
    bridge, store = daemon(None)                                             # not Linux, or not configured
    bridge.refresh_desktop_state()
    assert store.report_desktop_state.call_args.kwargs["capacity"] is None


# --- the backend --------------------------------------------------------------------------


@pytest.fixture
def env(tmp_path):
    db = Database(str(tmp_path / "placet.db"))
    db.init_db()
    config = PlacetConfig(agents={"claude": AgentSpec(tmux_session="agent-claude", token="tok-claude", allowed_targets=["human"])})
    return create_app(db=db, config=config, bridge_token="tok-bridge"), db


async def post_state(app, **body):
    async with agent_client(app, "tok-bridge") as bridge:
        return await bridge.post("/api/bridge/desktop-state", json={"archived": [], "not_archived": [], **body})


@pytest.mark.asyncio
async def test_the_numbers_are_stored_and_read_by_the_human(env):
    app, db = env
    async with human_client(app) as human:
        assert (await human.get("/api/claude-desktop/capacity")).json() == {}
    assert (await post_state(app, engines=6, cap=7)).status_code == 200
    async with human_client(app) as human:
        got = (await human.get("/api/claude-desktop/capacity")).json()
    assert (got["engines"], got["cap"]) == (6, 7) and got["observed_at"]
    async with agent_client(app, "tok-claude") as claude:
        assert (await claude.get("/api/claude-desktop/capacity")).status_code == 403


@pytest.mark.asyncio
async def test_odd_numbers_are_refused_and_partial_ones_ignored(env):
    app, db = env
    assert (await post_state(app, engines=-1, cap=7)).status_code == 422
    assert (await post_state(app, engines=5, cap=0)).status_code == 422
    assert (await post_state(app, engines=100000, cap=7)).status_code == 422
    assert (await post_state(app, engines=6)).status_code == 200            # a cap without a count means nothing
    async with human_client(app) as human:
        assert (await human.get("/api/claude-desktop/capacity")).json() == {}
