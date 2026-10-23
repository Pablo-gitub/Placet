"""RFC-16, "cancelled in the app": the user stops a Claude Code turn (Esc). No hook fires (Stop does
not run on a user interrupt), so the session looked busy forever and nothing was delivered to it.
The bridge reads the conversation's last message; the backend ends the turn without a reply."""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from backend.app.config import AgentSpec, PlacetConfig
from backend.app.database import Database
from backend.app.main import create_app
from backend.app.models import RequestCreate, RequestStatus
from bridge.bridge_daemon import BridgeDaemon
from bridge.claude_interrupts import last_turn_interrupted
from tests.helpers import agent_client, human_client

CONFIG = PlacetConfig(agents={
    "claude": AgentSpec(tmux_session="agent-claude", token="tok-claude", allowed_targets=["human", "codex"]),
    "codex": AgentSpec(tmux_session="agent-codex", token="tok-codex", allowed_targets=["human", "claude"]),
})
T0 = datetime(2026, 10, 23, 1, 0, tzinfo=timezone.utc)


def at(seconds):
    return (T0 + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def user(text, t, **extra):
    return {"type": "user", "timestamp": at(t), "message": {"role": "user", "content": [{"type": "text", "text": text}]}, **extra}


def assistant(text, t):
    return {"type": "assistant", "timestamp": at(t), "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def tool_result(t):
    return {"type": "user", "timestamp": at(t), "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": "ok"}]}}


def write(tmp_path, records):
    path = tmp_path / "s.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    return path


# --- reading the conversation --------------------------------------------------------------


@pytest.mark.parametrize("marker", ["[Request interrupted by user]", "[Request interrupted by user for tool use]"])
def test_an_interrupt_as_last_message_is_found_with_its_time(tmp_path, marker):
    path = write(tmp_path, [user("fai", 0), assistant("ok", 5), user(marker, 9),
                            {"type": "system", "timestamp": at(10), "content": "x"}])
    assert last_turn_interrupted(path) == T0 + timedelta(seconds=9)


def test_a_turn_that_went_on_after_the_interrupt_is_not_interrupted(tmp_path):
    assert last_turn_interrupted(write(tmp_path, [user("[Request interrupted by user]", 1), user("vai", 2)])) is None
    assert last_turn_interrupted(write(tmp_path, [user("[Request interrupted by user]", 1), assistant("ok", 2)])) is None
    assert last_turn_interrupted(write(tmp_path, [user("fai", 1), tool_result(2)])) is None


def test_sidechains_meta_and_broken_lines_are_ignored(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_text(json.dumps(user("[Request interrupted by user]", 3)) + "\n{broken\n"
                    + json.dumps(assistant("sub", 4) | {"isSidechain": True}) + "\n"
                    + json.dumps(user("hook text", 5, isMeta=True)) + "\n")
    assert last_turn_interrupted(path) == T0 + timedelta(seconds=3)
    assert last_turn_interrupted(tmp_path / "missing.jsonl") is None


def test_text_that_merely_mentions_the_marker_is_not_an_interrupt(tmp_path):
    path = write(tmp_path, [user('grep "[Request interrupted by user]" in the logs', 1)])
    assert last_turn_interrupted(path) is None


# --- the backend ---------------------------------------------------------------------------


@pytest.fixture
def setup(tmp_path):
    db = Database(str(tmp_path / "placet.db"))
    db.init_db()
    return create_app(db=db, config=CONFIG, bridge_token="tok-bridge"), db


async def event(client, session, **body):
    resp = await client.post(f"/api/sessions/{session}/events", json={"provider": "claude-code", **body})
    assert resp.status_code == 200, resp.text


async def running(app, db):
    req = db.create_request(RequestCreate(sender_id="codex", recipient_id="claude", prompt="fai tutto"))
    async with human_client(app) as human:
        await human.post(f"/api/requests/{req.id}/decision", json={"status": "APPROVED"})
    async with agent_client(app, "tok-claude") as claude:
        await event(claude, "c1", event="start")
        await claude.get("/api/sessions/c1/inbox")
        await event(claude, "c1", event="prompt", request_id=req.id)
    return req


async def report(app, when, sid="c1", token="tok-bridge"):
    async with agent_client(app, token) as bridge:
        return await bridge.post(f"/api/bridge/sessions/{sid}/interrupted", json={"at": when.isoformat()})


@pytest.mark.asyncio
async def test_busy_claude_sessions_are_listed_for_the_bridge(setup):
    app, db = setup
    await running(app, db)
    async with agent_client(app, "tok-bridge") as bridge:
        work = (await bridge.get("/api/bridge/work")).json()
    assert [s["id"] for s in work["claude_busy"]] == ["c1"] and work["claude_busy"][0]["since"]


@pytest.mark.asyncio
async def test_an_interrupt_after_the_turn_started_ends_it_without_a_reply(setup):
    app, db = setup
    req = await running(app, db)
    resp = await report(app, datetime.now(timezone.utc) + timedelta(seconds=1))
    assert resp.status_code == 200 and resp.json()["ended"] is True
    session = db.get_session("c1")
    assert session.state == "idle" and session.turn_request_id is None
    after = db.get_request(req.id)
    assert after.status == RequestStatus.EXECUTING and after.response is None and after.execution == "interrupted"
    assert "cancelled_in_app" in [e.event for e in db.list_events(req.id)]


@pytest.mark.asyncio
async def test_an_interrupt_older_than_the_turn_or_on_an_idle_session_changes_nothing(setup):
    app, db = setup
    req = await running(app, db)
    old = await report(app, datetime.now(timezone.utc) - timedelta(minutes=5))
    assert old.status_code == 200 and old.json()["ended"] is False
    assert db.get_session("c1").state == "busy" and db.get_request(req.id).execution == "started"
    async with agent_client(app, "tok-claude") as claude:
        await event(claude, "c1", event="stop", last_message="fatto")
    assert (await report(app, datetime.now(timezone.utc) + timedelta(seconds=1))).json()["ended"] is False
    assert (await report(app, datetime.now(timezone.utc), sid="nobody")).status_code == 404
    assert (await report(app, datetime.now(timezone.utc), token="tok-claude")).status_code == 403


# --- the bridge ----------------------------------------------------------------------------


def test_the_bridge_reports_an_interrupt_once_and_never_breaks(tmp_path, monkeypatch):
    path = write(tmp_path, [user("fai", 0), user("[Request interrupted by user]", 9)])
    store = MagicMock()
    bridge = BridgeDaemon(store=store, tmux=MagicMock(), channels={})
    monkeypatch.setattr("bridge.bridge_daemon.locate_transcript", lambda provider, sid: path)
    busy = [{"id": "c1", "since": at(-5)}]
    bridge.check_interrupts(busy, force=True)
    bridge.check_interrupts(busy, force=True)
    store.report_interrupted.assert_called_once_with("c1", T0 + timedelta(seconds=9))
    store.report_interrupted.reset_mock()
    bridge.check_interrupts([{"id": "c1", "since": at(20)}], force=True)       # a later turn: not this interrupt
    store.report_interrupted.assert_not_called()
    monkeypatch.setattr("bridge.bridge_daemon.locate_transcript", lambda provider, sid: (_ for _ in ()).throw(OSError()))
    bridge.check_interrupts([{"id": "c2", "since": at(-5)}], force=True)       # swallowed
    store.report_interrupted.assert_not_called()
