"""
RF-62: /compact and /effort typed into a Claude Code CLI's tmux pane through
a bridge job. Fixed command strings only, never text from the phone.
"""
import asyncio

import pytest


@pytest.fixture(autouse=True)
def no_settle_wait(monkeypatch):
    """The bridge waits a moment after Enter before reading the screen: not in tests."""
    monkeypatch.setattr("bridge.session_commands.SETTLE_SECONDS", 0)

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


async def start(app, agent, session_id, provider, entrypoint="cli", tmux="agent-claude", pane="%3"):
    body = {"event": "start", "provider": provider, "cwd": "/p", "entrypoint": entrypoint}
    if tmux:
        body["tmux_session"] = tmux
        body["tmux_pane"] = pane
        # Who runs in the pane: the tmux server, and the agent process with its start time.
        body.update({"tmux_server": "4242", "agent_pid": 777, "agent_started": "123456"})
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
    assert job["session_id"] == "s1" and job["tmux_pane"] == "%3"  # who, and the stable pane
    assert set(job) <= {"id", "kind", "command", "level", "tmux_session", "tmux_pane", "tmux_server", "agent_pid",
                        "agent_started", "session_id", "agent_id", "deadline"}  # no free text
    [event] = db.list_session_controls("s1")
    assert event["actor"].startswith("human") and event["command"] == "/compact" and event["state"] == "sent"


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
    assert [row["state"] for row in db.list_session_controls("s1")] == ["refused", "refused"]  # certainly not typed


class FakeTmux:
    """Records every target an operation was aimed at: it must always be the pane id."""

    def __init__(self, exists=True, idle=True, typing=None, pane_session="agent-claude", type_ok=True, enter_ok=True,
                 check_error=False, server_pid=4242, after="idle"):
        self.server_pid = server_pid
        self.after = after            # what the screen shows once Enter was pressed
        self.exists, self.idle, self.typing, self.pane_session = exists, idle, typing, pane_session
        self.check_error = check_error
        self.type_ok, self.enter_ok = type_ok, enter_ok
        self.sent, self.targets, self.typed = [], [], None

    def session_exists(self, name):
        return self.exists

    def pane_info(self, pane):
        self.targets.append(("pane_info", pane))
        if not self.exists:
            return None
        from types import SimpleNamespace
        return SimpleNamespace(server_pid=self.server_pid, pane_pid=900, session_name=self.pane_session)

    def is_session_idle(self, target, idle_patterns, busy_patterns):
        self.targets.append(("idle", target))
        return self.idle

    def seconds_since_input(self, name):
        return self.typing

    def input_idle_seconds(self, name):
        if self.check_error:
            from bridge.tmux_controller import TmuxCheckError
            raise TmuxCheckError("cannot list clients")
        return self.typing

    def check_idle(self, target, idle_patterns, busy_patterns):
        """The screen after Enter: idle prompt, still working, a dialog, or unreadable."""
        from bridge.tmux_controller import IdleCheck
        self.targets.append(("after", target))
        return {"idle": IdleCheck(True, "empty prompt and stable screen"),
                "busy": IdleCheck(False, "busy indicator: 'esc to interrupt'", busy_marker="esc to interrupt"),
                "changing": IdleCheck(False, "screen is changing"),
                "dialog": IdleCheck(False, "no empty input prompt recognized", "Apply the new effort level? [y/N]"),
                "unreadable": IdleCheck(False, "empty screen or unreadable session")}[self.after]

    def send_keys(self, target, text, enter=True):
        self.targets.append(("send", target))
        self.sent.append((target, text, enter))
        return True

    def type_text(self, target, text):
        self.targets.append(("send", target))
        self.typed = (target, text)
        self.typed_ok_flag = self.type_ok
        return self.type_ok

    def press_enter(self, target):
        self.targets.append(("send", target))
        if not self.enter_ok:
            return False
        self.sent.append((target, self.typed[1], True))
        return True


class FakeProcs:
    """The /proc questions of the bridge: is the agent process alive (same start), is it under the pane's process."""

    def __init__(self, alive=True, under_pane=True):
        self.is_alive, self.is_under = alive, under_pane

    def alive(self, pid, started):
        return self.is_alive and (pid, started) == (777, "123456")

    def descends(self, pid, ancestor):
        return self.is_under and ancestor == 900


def daemon(env, tmux, procs=None):
    app, db = env
    bridge = BridgeDaemon(db=db, config=CONFIG, tmux=tmux)
    bridge.procs = procs or FakeProcs()
    return bridge


JOB = {"id": "j", "kind": "session_command", "agent_id": "claude", "tmux_session": "agent-claude",
       "tmux_pane": "%3", "tmux_server": "4242", "agent_pid": 777, "agent_started": "123456",
       "session_id": "s1", "command": "compact"}


def test_the_bridge_types_only_the_fixed_command_into_the_configured_pane(env):
    tmux = FakeTmux()
    out = daemon(env, tmux).handle_job({**JOB, "text": "rm -rf /"})
    assert out == {"ok": True, "page": {"sent": "/compact", "needs_confirmation": False}}
    assert tmux.sent == [("%3", "/compact", True)]
    tmux = FakeTmux()
    daemon(env, tmux).handle_job({**JOB, "command": "effort", "level": "max"})
    assert tmux.sent == [("%3", "/effort max", True)]


@pytest.mark.parametrize("job,tmux,error", [
    ({"tmux_session": "somewhere-else"}, FakeTmux(), "wrong_session"),
    ({"tmux_pane": "agent-claude"}, FakeTmux(), "tmux_missing"),          # not a pane id: never a session name
    ({"tmux_pane": None}, FakeTmux(), "tmux_missing"),
    ({"agent_id": "nobody"}, FakeTmux(), "wrong_session"),
    ({}, FakeTmux(exists=False), "tmux_missing"),
    ({}, FakeTmux(pane_session="another-name"), "wrong_session"),        # the pane now belongs to another tmux session
    ({}, FakeTmux(idle=False), "not_idle"),
    ({}, FakeTmux(typing=3.0), "user_typing"),
    ({"command": "model"}, FakeTmux(), "invalid"),
    ({"command": "effort", "level": "extreme"}, FakeTmux(), "invalid"),
])
def test_the_bridge_refuses_what_is_not_safe(env, job, tmux, error):
    out = daemon(env, tmux).handle_job({**JOB, **job})
    assert out["ok"] is False and out["error"] == error
    assert tmux.sent == []


def test_every_tmux_operation_targets_the_stable_pane_id(env):
    """A pane switch between the check and the send cannot redirect the command."""
    tmux = FakeTmux()
    daemon(env, tmux).handle_job(JOB)
    aimed = {target for op, target in tmux.targets if op in ("idle", "send")}
    assert aimed == {"%3"}


@pytest.mark.asyncio
async def test_a_session_replaced_in_the_same_pane_is_refused_and_the_new_one_is_served(env):
    app, db = env
    await start(app, "claude", "old", "claude-code")                    # pane %3, session "agent-claude"
    await asyncio.sleep(0.01)
    await start(app, "claude", "new", "claude-code")                    # same tmux name and pane, later
    stale = await post(app, "/api/sessions/old/compact")
    assert stale.status_code == 409 and stale.json()["code"] == "session.pane_stale"
    seen = []
    ok = await post(app, "/api/sessions/new/compact", bridge_answer={"ok": True, "page": {"sent": "/compact"}}, seen=seen)
    assert ok.status_code == 200 and seen[0]["session_id"] == "new"
    assert db.list_session_controls("old") == []


@pytest.mark.asyncio
async def test_sessions_that_ended_or_sit_in_another_pane_do_not_make_it_stale(env):
    app, db = env
    await start(app, "claude", "old", "claude-code")
    await start(app, "claude", "other-pane", "claude-code", pane="%9")
    await asyncio.sleep(0.01)
    await start(app, "claude", "gone", "claude-code")
    db.end_session("gone")
    ok = await post(app, "/api/sessions/old/compact", bridge_answer={"ok": True, "page": {"sent": "/compact"}})
    assert ok.status_code == 200


@pytest.mark.asyncio
async def test_two_sessions_equally_recent_in_a_pane_are_ambiguous(env):
    app, db = env
    await start(app, "claude", "a", "claude-code")
    await start(app, "claude", "b", "claude-code")
    with db._get_connection() as conn:
        conn.execute("UPDATE sessions SET last_seen_at = '2026-10-22T10:00:00+00:00' WHERE id IN ('a', 'b')")
    resp = await post(app, "/api/sessions/a/compact")
    assert resp.status_code == 409 and resp.json()["code"] == "session.pane_ambiguous"


def test_normal_delivery_and_a_command_never_type_into_the_same_pane_at_once(env):
    """One lock per tmux session in the bridge: a delivery waits for a command in progress."""
    import threading
    app, db = env
    tmux = FakeTmux()
    bridge = daemon(env, tmux)
    lock = bridge.pane_lock("agent-claude")
    assert bridge.pane_lock("agent-claude") is lock and bridge.pane_lock("other") is not lock
    req = db.create_request(__import__("backend.app.models", fromlist=["RequestCreate"]).RequestCreate(
        sender_id="codex", recipient_id="claude", prompt="fai"))
    from backend.app.models import RequestStatus
    db.update_request_decision(req.id, RequestStatus.APPROVED, "human:p")
    finished = threading.Event()

    def deliver():
        bridge.process_pending_injections(injections=[db.get_request(req.id)], busy_agents=set(), busy_sessions=set())
        finished.set()

    with lock:                                  # a command is being typed
        worker = threading.Thread(target=deliver)
        worker.start()
        assert not finished.wait(0.3) and tmux.sent == []
    worker.join(5)
    assert finished.is_set() and [t for _, t, _ in tmux.sent][0].startswith("[Placet")


# --- the outcome is audited before dispatch and may be uncertain ------------------------------


@pytest.mark.asyncio
async def test_the_audit_row_exists_as_pending_before_the_bridge_is_asked(env):
    app, db = env
    await start(app, "claude", "s1", "claude-code")
    states = []

    def handle(job):
        states.append([row["state"] for row in db.list_session_controls("s1")])
        return {"ok": True, "page": {"sent": "/compact"}}

    task = asyncio.create_task(fake_bridge(app, handle, polls=1))
    await asyncio.sleep(0.05)
    async with human_client(app) as human:
        assert (await human.post("/api/sessions/s1/compact")).status_code == 200
    await task
    assert states == [["pending"]]
    assert [row["state"] for row in db.list_session_controls("s1")] == ["sent"]


@pytest.mark.asyncio
async def test_a_lost_reply_is_uncertain_and_never_retried(env, monkeypatch):
    app, db = env
    await start(app, "claude", "s1", "claude-code")
    monkeypatch.setattr("backend.app.bridge_jobs.JOB_TIMEOUT_SECONDS", 0.3)
    taken = []

    async def silent_bridge():
        async with agent_client(app, "tok-bridge") as bridge:
            for _ in range(3):
                resp = await bridge.get("/api/bridge/jobs?wait_seconds=1")
                if resp.status_code != 204:
                    taken.extend(resp.json()["jobs"])  # took it, never answers

    task = asyncio.create_task(silent_bridge())
    await asyncio.sleep(0.05)
    resp = await post(app, "/api/sessions/s1/compact")
    await task
    assert resp.status_code == 502 and resp.json()["code"] == "session_command.uncertain"
    assert len(taken) == 1  # no automatic retry
    [row] = db.list_session_controls("s1")
    assert row["state"] == "uncertain"
    assert resp.json()["params"]["control_id"] == row["id"]


@pytest.mark.asyncio
async def test_text_typed_but_enter_failed_is_uncertain(env):
    app, db = env
    await start(app, "claude", "s1", "claude-code")
    resp = await post(app, "/api/sessions/s1/compact", bridge_answer={"ok": False, "error": "enter_failed"})
    assert resp.status_code == 502 and resp.json()["code"] == "session_command.uncertain"
    assert [row["state"] for row in db.list_session_controls("s1")] == ["uncertain"]


def test_the_bridge_separates_not_typed_from_typed_without_enter(env):
    tmux = FakeTmux(type_ok=False)
    out = daemon(env, tmux).handle_job(JOB)
    assert out["error"] == "send_failed" and tmux.sent == []
    tmux = FakeTmux(enter_ok=False)
    out = daemon(env, tmux).handle_job(JOB)
    assert out["ok"] is False and out["error"] == "enter_failed"
    assert tmux.typed == ("%3", "/compact") and tmux.sent == []   # typed, never confirmed, not retried


@pytest.mark.asyncio
async def test_a_refusal_is_audited_as_refused(env):
    app, db = env
    await start(app, "claude", "s1", "claude-code")
    resp = await post(app, "/api/sessions/s1/compact", bridge_answer={"ok": False, "error": "not_idle"})
    assert resp.status_code == 409 and resp.json()["code"] == "session_command.not_idle"
    assert [row["state"] for row in db.list_session_controls("s1")] == ["refused"]


@pytest.mark.asyncio
async def test_an_offline_bridge_means_certainly_not_sent(env):
    app, db = env
    await start(app, "claude", "s1", "claude-code")
    offline = await post(app, "/api/sessions/s1/compact")  # nobody ever polled
    assert offline.status_code == 503 and offline.json()["code"] == "bridge.offline"
    assert [row["state"] for row in db.list_session_controls("s1")] == ["refused"]


@pytest.mark.asyncio
async def test_a_pending_command_blocks_a_second_one(env):
    app, db = env
    await start(app, "claude", "s1", "claude-code")
    db.begin_session_control("s1", "human:p", "/compact")
    resp = await post(app, "/api/sessions/s1/effort", {"level": "low"})
    assert resp.status_code == 409 and resp.json()["code"] == "session.control_pending"


def test_only_the_backends_startup_turns_pending_rows_into_uncertain(tmp_path):
    path = str(tmp_path / "placet.db")
    db = Database(path)
    db.init_db()
    pending = db.begin_session_control("s1", "human:p", "/compact")
    done = db.begin_session_control("s2", "human:p", "/effort low")
    db.settle_session_control(done, "sent")
    other = Database(path)                       # the bridge's LocalStore opens the database too
    other.init_db()
    assert other.list_session_controls("s1")[0]["state"] == "pending"
    restarted = Database(path)
    restarted.init_db()
    create_app(db=restarted, config=CONFIG, bridge_token="tok-bridge")     # the backend starts
    assert restarted.list_session_controls("s1")[0]["id"] == pending
    assert restarted.list_session_controls("s1")[0]["state"] == "uncertain"
    assert restarted.list_session_controls("s2")[0]["state"] == "sent"


def test_rows_written_before_states_existed_count_as_sent(tmp_path):
    import sqlite3
    path = str(tmp_path / "legacy.db")
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE session_control_events (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,"
                     " at TEXT NOT NULL, actor TEXT NOT NULL, command TEXT NOT NULL)")
        conn.execute("INSERT INTO session_control_events (session_id, at, actor, command) VALUES ('s1', '2026-10-22T00:00:00', 'human', '/compact')")
    db = Database(path)
    db.init_db()
    assert [row["state"] for row in db.list_session_controls("s1")] == ["sent"]


# --- P2: no client attached is not the same as "cannot check" ----------------------------------


class _Run:
    def __init__(self, stdout="", error=None):
        self.stdout, self.error = stdout, error

    def __call__(self, *a, **k):
        if self.error:
            raise self.error
        import types
        return types.SimpleNamespace(stdout=self.stdout, returncode=0)


def test_input_recency_tells_no_client_from_a_failed_check(monkeypatch):
    import subprocess
    import time
    from bridge.tmux_controller import TmuxCheckError, TmuxController
    controller = TmuxController()
    monkeypatch.setattr("bridge.tmux_controller.subprocess.run", _Run(f"{int(time.time()) - 6}\n"))
    assert 4 <= controller.input_idle_seconds("s") <= 8
    monkeypatch.setattr("bridge.tmux_controller.subprocess.run", _Run(""))
    assert controller.input_idle_seconds("s") is None                  # nobody attached: nobody typing
    monkeypatch.setattr("bridge.tmux_controller.subprocess.run", _Run(error=subprocess.CalledProcessError(1, "tmux")))
    with pytest.raises(TmuxCheckError):
        controller.input_idle_seconds("s")
    monkeypatch.setattr("bridge.tmux_controller.subprocess.run", _Run(error=FileNotFoundError()))
    with pytest.raises(TmuxCheckError):
        controller.input_idle_seconds("s")
    monkeypatch.setattr("bridge.tmux_controller.subprocess.run", _Run("not-a-number\n"))
    with pytest.raises(TmuxCheckError):
        controller.input_idle_seconds("s")


def test_the_old_check_keeps_its_behaviour_for_normal_deliveries(monkeypatch):
    import subprocess
    from bridge.tmux_controller import TmuxController
    monkeypatch.setattr("bridge.tmux_controller.subprocess.run", _Run(error=subprocess.CalledProcessError(1, "tmux")))
    assert TmuxController().seconds_since_input("s") is None


def test_a_command_fails_closed_when_the_input_cannot_be_checked(env):
    tmux = FakeTmux(check_error=True)
    out = daemon(env, tmux).handle_job(JOB)
    assert out["ok"] is False and out["error"] == "check_failed" and tmux.sent == [] and tmux.typed is None
    tmux = FakeTmux(typing=None)                     # no client attached: allowed
    assert daemon(env, tmux).handle_job(JOB)["ok"] is True


@pytest.mark.asyncio
async def test_an_unchecked_input_is_a_refusal_not_an_uncertain_outcome(env):
    app, db = env
    await start(app, "claude", "s1", "claude-code")
    resp = await post(app, "/api/sessions/s1/compact", bridge_answer={"ok": False, "error": "check_failed"})
    assert resp.status_code == 409 and resp.json()["code"] == "session_command.check_failed"
    assert [row["state"] for row in db.list_session_controls("s1")] == ["refused"]


# --- P1.1: a pane id is only unique within one tmux server, and only while its process lives ----


@pytest.mark.parametrize("tmux,procs,error", [
    (FakeTmux(server_pid=9999), None, "server_mismatch"),                    # tmux restarted: %0 is somebody else's
    (FakeTmux(), FakeProcs(alive=False), "agent_gone"),                      # the session's process is dead (a ghost)
    (FakeTmux(), FakeProcs(under_pane=False), "agent_not_in_pane"),          # alive, but not what runs in this pane
])
def test_a_reused_pane_id_or_a_dead_process_is_refused(env, tmux, procs, error):
    out = daemon(env, tmux, procs).handle_job(JOB)
    assert out["ok"] is False and out["error"] == error and tmux.sent == [] and tmux.typed is None


@pytest.mark.parametrize("missing", ["tmux_server", "agent_pid", "agent_started"])
def test_a_job_without_the_process_identity_is_refused(env, missing):
    tmux = FakeTmux()
    out = daemon(env, tmux).handle_job({**JOB, missing: None})
    assert out["ok"] is False and out["error"] == "identity_unknown" and tmux.sent == []


@pytest.mark.asyncio
async def test_after_a_tmux_restart_the_ghost_session_cannot_type_into_the_new_occupant(env):
    """A (closed without SessionEnd) kept %0; B now holds %0 in a new tmux server and reports no pane."""
    app, db = env
    await start(app, "claude", "ghost", "claude-code", pane="%0")
    await start(app, "claude", "newcomer", "claude-code", tmux=None)       # old hook / .placet-off: no pane report
    tmux = FakeTmux(server_pid=5151)                                        # the restarted server
    bridge = daemon(env, tmux)
    task = asyncio.create_task(fake_bridge(app, bridge.handle_job, polls=1))
    await asyncio.sleep(0.05)
    async with human_client(app) as human:
        resp = await human.post("/api/sessions/ghost/compact")
    await task
    assert resp.status_code == 409 and resp.json()["code"] == "session_command.server_mismatch"
    assert tmux.sent == [] and tmux.typed is None
    assert [row["state"] for row in db.list_session_controls("ghost")] == ["refused"]


# --- the hook side and /proc ------------------------------------------------------------------


def fake_proc(root, pid, ppid, comm, starttime):
    folder = root / str(pid)
    folder.mkdir(parents=True)
    (folder / "comm").write_text(comm + "\n")
    fields = ["S", str(ppid)] + ["0"] * 17 + [str(starttime)] + ["0"] * 10
    (folder / "stat").write_text(f"{pid} ({comm}) " + " ".join(fields) + "\n")


def test_procs_tell_alive_from_dead_and_descendants_from_strangers(tmp_path):
    from bridge.procs import Procs
    root = tmp_path / "proc"
    fake_proc(root, 900, 1, "bash", 50)         # the pane's process
    fake_proc(root, 800, 900, "node", 60)
    fake_proc(root, 777, 800, "claude", 70)     # the agent, under the pane
    fake_proc(root, 555, 1, "claude", 80)       # another claude, elsewhere
    procs = Procs(root)
    assert procs.alive(777, "70") and not procs.alive(777, "71") and not procs.alive(123, "70")
    assert procs.descends(777, 900) and not procs.descends(555, 900) and not procs.descends(777, 555)
    assert procs.descends(900, 900)
    (root / "777" / "stat").write_text("777 (claude) Z 800 " + " ".join(["0"] * 17) + " 70 " + " ".join(["0"] * 10))
    assert not procs.alive(777, "70")           # a zombie is not running


def test_the_hook_finds_its_agent_process_and_the_tmux_server(tmp_path, monkeypatch):
    from hooks import placet_common
    root = tmp_path / "proc"
    fake_proc(root, 300, 1, "bash", 10)
    fake_proc(root, 200, 300, "claude", 4321)
    fake_proc(root, 100, 200, "sh", 20)         # the hook's own parent shell
    assert placet_common.agent_process(start_pid=100, proc_root=root) == (200, "4321")
    assert placet_common.agent_process(start_pid=300, proc_root=root) is None
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,5151,0")
    assert placet_common.tmux_server_pid() == "5151"
    monkeypatch.setenv("TMUX", "garbage")
    assert placet_common.tmux_server_pid() is None
    monkeypatch.delenv("TMUX")
    assert placet_common.tmux_server_pid() is None


def test_the_claude_hook_reports_the_identity(monkeypatch):
    from hooks import claude_code
    monkeypatch.setattr(claude_code, "_tmux_session", lambda: "work")
    monkeypatch.setenv("TMUX", "x,5151,0")
    monkeypatch.setenv("TMUX_PANE", "%3")
    monkeypatch.setattr(claude_code.placet_common, "agent_process", lambda **k: (200, "4321"))
    payload = claude_code.build_event({"hook_event_name": "UserPromptSubmit", "cwd": "/p", "prompt": "x"})
    assert (payload["tmux_server"], payload["agent_pid"], payload["agent_started"]) == ("5151", 200, "4321")


# --- P1.2: /effort may open a confirmation dialog; "sent" must not hide it ----------------------


@pytest.mark.parametrize("after,needs", [("idle", False), ("busy", False), ("changing", False), ("dialog", True)])
def test_the_screen_after_enter_decides_between_sent_and_needs_confirmation(env, after, needs):
    tmux = FakeTmux(after=after)
    out = daemon(env, tmux).handle_job({**JOB, "command": "effort", "level": "high"})
    assert out == {"ok": True, "page": {"sent": "/effort high", "needs_confirmation": needs}}
    assert len(tmux.sent) == 1                                   # typed once, never retried
    assert ("after", "%3") in tmux.targets                       # the same stable pane


def test_an_unreadable_screen_after_enter_is_not_claimed_as_sent(env):
    tmux = FakeTmux(after="unreadable")
    out = daemon(env, tmux).handle_job(JOB)
    assert out["ok"] is False and out["error"] == "unverified"


def test_the_bridge_waits_inside_the_lock_before_reading_the_screen(env, monkeypatch):
    """A delivery must not type into a dialog that has not appeared yet."""
    import threading
    monkeypatch.setattr("bridge.session_commands.SETTLE_SECONDS", 0.3)
    tmux = FakeTmux()
    bridge = daemon(env, tmux)
    worker = threading.Thread(target=bridge.handle_job, args=(JOB,))
    worker.start()
    for _ in range(100):
        if tmux.sent:
            break
        import time
        time.sleep(0.01)
    assert tmux.sent and bridge.pane_lock("agent-claude").locked()      # Enter pressed, lock still held
    worker.join(5)
    assert not bridge.pane_lock("agent-claude").locked()


@pytest.mark.asyncio
async def test_a_dialog_is_reported_as_needs_confirmation_not_sent_not_uncertain(env):
    app, db = env
    await start(app, "claude", "s1", "claude-code")
    resp = await post(app, "/api/sessions/s1/effort", {"level": "high"},
                      bridge_answer={"ok": True, "page": {"sent": "/effort high", "needs_confirmation": True}})
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "needs_confirmation" and body["command"] == "/effort high"
    assert [row["state"] for row in db.list_session_controls("s1")] == ["needs_confirmation"]
    # It does not block the next control, and the plain case still says sent.
    again = await post(app, "/api/sessions/s1/compact", bridge_answer={"ok": True, "page": {"sent": "/compact", "needs_confirmation": False}})
    assert again.json()["state"] == "sent"


@pytest.mark.asyncio
async def test_an_unverified_enter_is_uncertain(env):
    app, db = env
    await start(app, "claude", "s1", "claude-code")
    resp = await post(app, "/api/sessions/s1/compact", bridge_answer={"ok": False, "error": "unverified"})
    assert resp.status_code == 502 and resp.json()["code"] == "session_command.uncertain"
    assert [row["state"] for row in db.list_session_controls("s1")] == ["uncertain"]


# --- P2 ---------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_job_carries_a_deadline_and_the_bridge_refuses_after_it(env):
    import time
    app, db = env
    await start(app, "claude", "s1", "claude-code")
    seen = []
    await post(app, "/api/sessions/s1/compact", bridge_answer={"ok": True, "page": {"sent": "/compact", "needs_confirmation": False}}, seen=seen)
    assert seen[0]["deadline"] > time.time() - 1
    tmux = FakeTmux()
    out = daemon(env, tmux).handle_job({**JOB, "deadline": time.time() - 1})
    assert out["ok"] is False and out["error"] == "expired" and tmux.sent == [] and tmux.typed is None


def test_a_job_that_waited_for_the_lock_past_its_deadline_is_refused(env):
    import threading
    import time
    tmux = FakeTmux()
    bridge = daemon(env, tmux)
    lock = bridge.pane_lock("agent-claude")
    result = []
    lock.acquire()                                             # a delivery holds the terminal
    worker = threading.Thread(target=lambda: result.append(bridge.handle_job({**JOB, "deadline": time.time() + 0.2})))
    worker.start()
    time.sleep(0.4)
    lock.release()
    worker.join(5)
    assert result[0]["error"] == "expired" and tmux.typed is None


@pytest.mark.asyncio
async def test_a_job_nobody_took_is_a_certain_refusal(env, monkeypatch):
    app, db = env
    await start(app, "claude", "s1", "claude-code")
    monkeypatch.setattr("backend.app.bridge_jobs.JOB_TIMEOUT_SECONDS", 0.2)
    async with agent_client(app, "tok-bridge") as bridge:
        assert (await bridge.get("/api/bridge/jobs")).status_code == 204        # the bridge is alive...
        resp = await post(app, "/api/sessions/s1/compact")                       # ...but takes nothing
    assert resp.status_code == 504 and resp.json()["code"] == "bridge.not_taken"
    assert [row["state"] for row in db.list_session_controls("s1")] == ["refused"]


@pytest.mark.asyncio
async def test_a_bridge_that_does_not_know_the_job_is_a_refusal(env):
    app, db = env
    await start(app, "claude", "s1", "claude-code")
    resp = await post(app, "/api/sessions/s1/compact", bridge_answer={"ok": False, "error": "unknown_job"})
    assert resp.status_code == 409 and resp.json()["code"] == "session_command.unknown_job"
    assert [row["state"] for row in db.list_session_controls("s1")] == ["refused"]


def test_a_typing_timeout_is_uncertain_not_a_refusal(env, monkeypatch):
    from bridge.tmux_controller import TmuxTimeout
    tmux = FakeTmux()

    def hangs(target, text):
        raise TmuxTimeout("tmux did not answer")
    tmux.type_text = hangs
    out = daemon(env, tmux).handle_job(JOB)
    assert out["ok"] is False and out["error"] == "type_timeout"


@pytest.mark.asyncio
async def test_a_typing_timeout_is_audited_uncertain(env):
    app, db = env
    await start(app, "claude", "s1", "claude-code")
    resp = await post(app, "/api/sessions/s1/compact", bridge_answer={"ok": False, "error": "type_timeout"})
    assert resp.status_code == 502 and resp.json()["code"] == "session_command.uncertain"
    assert [row["state"] for row in db.list_session_controls("s1")] == ["uncertain"]


def test_the_controller_times_out_typing_and_capturing(monkeypatch):
    import subprocess
    from bridge.tmux_controller import TmuxController, TmuxTimeout
    seen = {}

    def run(*a, **k):
        seen.update(k)
        raise subprocess.TimeoutExpired("tmux", 5)
    monkeypatch.setattr("bridge.tmux_controller.subprocess.run", run)
    controller = TmuxController()
    with pytest.raises(TmuxTimeout):
        controller.type_text("%3", "/compact")
    assert controller.capture_pane("%3") == "" and seen.get("timeout")       # bounded, and a timeout reads as empty


def test_two_pending_controls_for_one_session_cannot_exist(tmp_path):
    from backend.app.database import ControlPending
    db = Database(str(tmp_path / "placet.db"))
    db.init_db()
    first = db.begin_session_control("s1", "human:p", "/compact")
    with pytest.raises(ControlPending):
        db.begin_session_control("s1", "human:p", "/effort low")
    db.begin_session_control("s2", "human:p", "/compact")                    # another session is free
    db.settle_session_control(first, "sent")
    db.begin_session_control("s1", "human:p", "/effort low")


@pytest.mark.asyncio
async def test_a_session_with_a_request_being_delivered_or_executed_takes_no_command(env):
    from backend.app.models import RequestCreate
    app, db = env
    await start(app, "claude", "s1", "claude-code")
    req = db.create_request(RequestCreate(sender_id="codex", recipient_id="claude", prompt="fai"))
    for status in ("APPROVED", "COMPLETED"):
        with db._get_connection() as conn:
            conn.execute("UPDATE requests SET status = ?, recipient_session_id = 's1' WHERE id = ?", (status, req.id))
        ok = await post(app, "/api/sessions/s1/compact", bridge_answer={"ok": True, "page": {"sent": "/compact", "needs_confirmation": False}})
        assert ok.status_code == 200, status
    for status in ("DELIVERING", "EXECUTING"):
        with db._get_connection() as conn:
            conn.execute("UPDATE requests SET status = ? WHERE id = ?", (status, req.id))
        resp = await post(app, "/api/sessions/s1/compact")
        assert resp.status_code == 409 and resp.json()["code"] == "session.has_request_running", status
