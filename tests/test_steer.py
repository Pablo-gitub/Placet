"""'Subito': a message inside the running turn, after the next action (RF-35)."""
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from backend.app.models import RequestStatus
from bridge import steer as steer_files
from bridge.bridge_daemon import BridgeDaemon
from bridge.channels import ChannelError
from bridge.stores import HttpStore
from bridge.transcripts import parse_claude
from hooks import install
from tests.helpers import agent_client, human_client
from tests.test_session_messages import setup, start  # noqa: F401

HOOKS = Path(__file__).resolve().parent.parent / "hooks"


@pytest.fixture(autouse=True)
def private_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("PLACET_STEER_DIR", str(tmp_path / "steer"))
    monkeypatch.setenv("PLACET_SPOOL_DIR", str(tmp_path / "spool"))
    return tmp_path


async def busy(app, agent, session_id, provider):
    await start(app, agent, session_id, provider)
    async with agent_client(app, f"tok-{agent}") as client:
        await client.post(f"/api/sessions/{session_id}/events", json={"event": "prompt"})


async def write_now(app, session_id, text="fermati e scrivi OK"):
    async with human_client(app) as human:
        resp = await human.post(f"/api/sessions/{session_id}/messages", json={"text": text, "mode": "now"})
        assert resp.status_code == 201
        return resp.json()


def daemon_for(app, channels=None):
    return BridgeDaemon(store=HttpStore(token="tok-bridge", client=TestClient(app)), tmux=MagicMock(),
                        channels=channels or {})


def run_hook(tmp_path, session_id="c1"):
    event = {"session_id": session_id, "cwd": str(tmp_path), "hook_event_name": "PostToolUse"}
    res = subprocess.run([sys.executable, str(HOOKS / "steer.py"), "--agent", "claude"],
                         input=json.dumps(event), capture_output=True, text=True, timeout=10,
                         env={"PATH": "/usr/bin", "HOME": str(tmp_path),
                              "PLACET_STEER_DIR": str(tmp_path / "steer"),
                              "PLACET_SPOOL_DIR": str(tmp_path / "spool")})
    return res.stdout


@pytest.mark.asyncio
async def test_claude_gets_it_after_its_next_action(setup, private_dirs):  # noqa: F811
    app, db = setup
    await busy(app, "claude", "c1", "claude-code")
    req = await write_now(app, "c1")
    assert req["delivery_mode"] == "now" and req["status"] == "APPROVED"

    daemon_for(app).run_loop_step()
    assert db.get_request(req["id"]).delivery_channel == "steer"
    out = json.loads(run_hook(private_dirs))
    context = out["hookSpecificOutput"]["additionalContext"]
    assert context.startswith(f"[Placet Request #{req['id']} dall'utente]: fermati e scrivi OK")
    assert "turno in corso" in context
    assert run_hook(private_dirs) == ""  # taken once

    # The hook's event, forwarded from the local queue.
    event = json.loads(next((private_dirs / "spool").glob("*.json")).read_text())
    async with agent_client(app, "tok-claude") as claude:
        await claude.post("/api/sessions/c1/events", json=event["body"])
        r = db.get_request(req["id"])
        assert (r.status, r.delivery, r.execution) == (RequestStatus.EXECUTING, "accepted", "started")
        await claude.post("/api/sessions/c1/events", json={"event": "stop", "last_message": "OK"})
    r = db.get_request(req["id"])
    assert (r.status, r.execution, r.reply) == (RequestStatus.COMPLETED, "completed", "ambiguous")


@pytest.mark.asyncio
async def test_a_turn_that_ends_first_puts_it_back_in_the_queue(setup, private_dirs):  # noqa: F811
    app, db = setup
    await busy(app, "claude", "c1", "claude-code")
    req = await write_now(app, "c1")
    daemon = daemon_for(app)
    daemon.run_loop_step()
    async with agent_client(app, "tok-claude") as claude:
        await claude.post("/api/sessions/c1/events", json={"event": "stop"})
    daemon.run_loop_step()
    assert not (private_dirs / "steer" / "c1.json").exists()
    assert run_hook(private_dirs) == ""  # nothing left to insert twice
    r = db.get_request(req["id"])
    assert r.status == RequestStatus.APPROVED and r.delivery == "waiting"
    async with agent_client(app, "tok-claude") as claude:  # the waiter delivers it as usual
        mail = (await claude.get("/api/sessions/c1/inbox")).json()
    assert mail["request_id"] == req["id"]


@pytest.mark.asyncio
async def test_antigravity_queues_it_for_the_next_model_call(setup):  # noqa: F811
    app, db = setup
    await busy(app, "gemini", "ag1", "antigravity")
    req = await write_now(app, "ag1")
    agy = MagicMock()
    daemon_for(app, {"antigravity": agy}).run_loop_step()
    session, text = agy.send.call_args.args
    assert session == "ag1" and agy.send.call_args.kwargs == {"now": True}
    assert db.get_request(req["id"]).status == RequestStatus.EXECUTING
    async with agent_client(app, "tok-gemini") as gemini:
        await gemini.post("/api/sessions/ag1/events", json={"event": "stop", "last_message": "fatto"})
    assert db.get_request(req["id"]).status == RequestStatus.COMPLETED


@pytest.mark.asyncio
async def test_codex_and_failures_fall_back_to_the_end_of_the_turn(setup):  # noqa: F811
    app, db = setup
    await busy(app, "codex", "cx1", "codex")
    codex_req = await write_now(app, "cx1")
    await busy(app, "gemini", "ag1", "antigravity")
    agy_req = await write_now(app, "ag1")
    agy = MagicMock()
    agy.send.side_effect = ChannelError("Antigravity is not running")
    codex = MagicMock()
    daemon_for(app, {"antigravity": agy, "codex": codex}).run_loop_step()
    codex.send.assert_not_called()  # Codex: not while it works
    assert db.get_request(codex_req["id"]).status == RequestStatus.APPROVED
    assert db.get_request(agy_req["id"]).status == RequestStatus.APPROVED


def test_the_inserted_message_shows_in_the_claude_conversation():
    entries = parse_claude({"type": "attachment", "timestamp": "t", "attachment": {
        "type": "hook_additional_context", "content": ["[Placet Request #9 dall'utente]: fermati\nÈ un messaggio..."]}})
    assert entries[0]["role"] == "user" and entries[0]["via"] == "placet" and entries[0]["request_id"] == 9
    other = parse_claude({"type": "attachment", "attachment": {
        "type": "hook_additional_context", "content": ["lint: 3 warnings"]}})
    assert other == []


def test_the_installer_adds_the_cheap_steer_hook(tmp_path):
    install.apply("claude", "/opt/placet/hooks", False, False, home=tmp_path)
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    commands = [h["command"] for g in settings["hooks"]["PostToolUse"] for h in g["hooks"]]
    assert any("steer.py --agent claude" in c and c.startswith("sh -c") for c in commands)
    install.apply("claude", "/opt/placet/hooks", True, False, home=tmp_path)
    assert "hooks" not in json.loads((tmp_path / ".claude" / "settings.json").read_text())


def test_only_one_message_waits_per_session(private_dirs):
    steer_files.leave("c1", 1, "uno")
    assert not steer_files.take_back("c1", 2)  # not that one: left in place
    assert steer_files.take_back("c1", 1)
