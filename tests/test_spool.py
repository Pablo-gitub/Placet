"""Local event queue of the hooks and its forwarding by the bridge (RF-28)."""
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

from bridge.spool_forwarder import RETRY, SpoolForwarder
from hooks import spool

HOOKS = Path(__file__).resolve().parent.parent / "hooks"


def queued(folder):
    return sorted(p.name for p in folder.iterdir() if p.suffix == ".json")


def test_write_event_is_private_and_ordered(tmp_path):
    folder = tmp_path / "spool"
    first = spool.write_event("claude", "s1", {"event": "prompt"}, folder)
    second = spool.write_event("claude", "s1", {"event": "stop"}, folder)
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    assert stat.S_IMODE(first.stat().st_mode) == 0o600
    assert queued(folder) == [first.name, second.name]
    event = json.loads(first.read_text())
    assert event["agent"] == "claude" and event["session_id"] == "s1" and event["body"] == {"event": "prompt"}
    assert not [p for p in folder.iterdir() if p.name.startswith(".")]


def test_a_full_queue_drops_new_events(tmp_path, monkeypatch):
    monkeypatch.setattr(spool, "MAX_SPOOL_BYTES", 10)
    folder = tmp_path / "spool"
    assert spool.write_event("claude", "s1", {"event": "start"}, folder)
    assert spool.write_event("claude", "s1", {"event": "stop"}, folder) is None


def test_hooks_write_to_the_queue_without_network(tmp_path):
    env = {"PATH": "/usr/bin", "HOME": str(tmp_path), "PLACET_SPOOL_DIR": str(tmp_path / "q"),
           "PLACET_API_URL": "http://127.0.0.1:9"}
    claude = {"hook_event_name": "Stop", "session_id": "c1", "cwd": str(tmp_path),
              "last_assistant_message": "FATTO"}
    subprocess.run([sys.executable, str(HOOKS / "claude_code.py"), "--agent", "claude"],
                   input=json.dumps(claude), text=True, env=env, timeout=10, check=True)
    agy = {"conversationId": "g1", "workspacePaths": [str(tmp_path)]}
    subprocess.run([sys.executable, str(HOOKS / "antigravity.py"), "--agent", "gemini", "--event", "SessionStart"],
                   input=json.dumps(agy), text=True, env=env, timeout=10, check=True)
    events = [json.loads((tmp_path / "q" / n).read_text()) for n in queued(tmp_path / "q")]
    assert {(e["agent"], e["session_id"], e["body"]["event"]) for e in events} == {
        ("claude", "c1", "stop"), ("gemini", "g1", "start")}
    assert [e for e in events if e["agent"] == "claude"][0]["body"]["last_message"] == "FATTO"

    (tmp_path / ".placet-off").touch()
    subprocess.run([sys.executable, str(HOOKS / "claude_code.py"), "--agent", "claude"],
                   input=json.dumps(claude), text=True, env=env, timeout=10, check=True)
    assert len(queued(tmp_path / "q")) == 2


class Backend:
    def __init__(self, answers=None):
        self.answers = list(answers or [])
        self.calls = []

    def __call__(self, agent, session_id, body):
        self.calls.append((agent, session_id, body))
        return self.answers.pop(0) if self.answers else 200


def test_forwards_in_order_and_deletes_after_success(tmp_path):
    folder = tmp_path / "spool"
    for kind in ("start", "prompt", "stop"):
        spool.write_event("claude", "s1", {"event": kind}, folder)
    backend = Backend()
    assert SpoolForwarder(backend, folder).drain()["sent"] == 3
    assert [b["event"] for _, _, b in backend.calls] == ["start", "prompt", "stop"]
    assert all(b["event_id"] for _, _, b in backend.calls)
    assert queued(folder) == []


def test_a_retryable_error_holds_the_session_but_not_the_others(tmp_path):
    folder = tmp_path / "spool"
    spool.write_event("claude", "s1", {"event": "prompt"}, folder)
    spool.write_event("claude", "s1", {"event": "stop"}, folder)
    spool.write_event("codex", "s2", {"event": "stop"}, folder)
    backend = Backend([503, 200])
    forwarder = SpoolForwarder(backend, folder)
    counts = forwarder.drain()
    assert counts == {"sent": 1, "failed": 0, "dropped": 0, "waiting": 2}
    assert [(s, b["event"]) for _, s, b in backend.calls] == [("s1", "prompt"), ("s2", "stop")]
    assert forwarder.backoff > 0
    # Next pass: the same event again, with the same event_id, then the rest.
    first_id = backend.calls[0][2]["event_id"]
    backend.answers = [RETRY]
    forwarder.drain()
    backend.answers = []
    assert forwarder.drain()["sent"] == 2
    assert backend.calls[-2][2]["event_id"] == first_id and forwarder.backoff == 0
    assert queued(folder) == []


def test_refused_and_malformed_events_go_to_failed(tmp_path):
    folder = tmp_path / "spool"
    spool.write_event("claude", "s1", {"event": "start"}, folder)
    (folder / "zz-broken.json").write_text("{ nope")
    spool.write_event("claude", "s1", {"event": "stop"}, folder)
    backend = Backend([422, 200])
    assert SpoolForwarder(backend, folder).drain() == {"sent": 1, "failed": 2, "dropped": 0, "waiting": 0}
    assert len(list((folder / "failed").iterdir())) == 2 and queued(folder) == []


def test_excluded_projects_and_stale_temporaries_are_dropped(tmp_path):
    folder = tmp_path / "spool"
    project = tmp_path / "proj"
    project.mkdir()
    spool.write_event("claude", "s1", {"event": "start", "cwd": str(project)}, folder)
    (project / ".placet-off").touch()
    stale = folder / ".old.json.tmp"
    stale.write_text("{}")
    os.utime(stale, (time.time() - 7200, time.time() - 7200))
    backend = Backend()
    assert SpoolForwarder(backend, folder).drain()["dropped"] == 1
    assert backend.calls == [] and not stale.exists()


def test_only_one_forwarder_at_a_time(tmp_path):
    first = SpoolForwarder(Backend(), tmp_path / "spool")
    assert first.acquire()
    assert not SpoolForwarder(Backend(), tmp_path / "spool").acquire()


# --- The backend applies a forwarded event once ----------------------------------------

import pytest  # noqa: E402

from tests.test_session_messages import setup, start, write  # noqa: E402,F401
from tests.helpers import agent_client  # noqa: E402
from backend.app.models import RequestStatus  # noqa: E402


@pytest.mark.asyncio
async def test_an_event_forwarded_twice_is_applied_once(setup):  # noqa: F811
    app, db = setup
    await start(app, "claude", "c1", "claude-code")
    req_id = (await write(app, "c1", "prima")).json()["id"]
    async with agent_client(app, "tok-claude") as claude:
        await claude.get("/api/sessions/c1/inbox")
        prompt = {"event": "prompt", "request_id": req_id, "event_id": "e-prompt"}
        assert "duplicate" not in (await claude.post("/api/sessions/c1/events", json=prompt)).json()
        stop = {"event": "stop", "last_message": "Fatto.", "event_id": "e-stop"}
        await claude.post("/api/sessions/c1/events", json=stop)
        # The bridge did not see the answer and sends both again.
        assert (await claude.post("/api/sessions/c1/events", json=prompt)).json()["duplicate"]
        assert (await claude.post("/api/sessions/c1/events", json=stop)).json()["duplicate"]
    assert db.get_request(req_id).status == RequestStatus.COMPLETED
    assert db.get_session("c1").state == "idle"
