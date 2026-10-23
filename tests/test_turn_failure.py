"""A turn ended by an API error (Claude Code StopFailure: rate limit, overload, auth...):
the request is not concluded, the session is idle, and the user is told why."""
import pytest

from backend.app.config import AgentSpec, PlacetConfig
from backend.app.database import Database
from backend.app.main import create_app
from backend.app.models import RequestCreate, RequestStatus
from hooks.claude_code import build_event
from hooks.install import claude_hooks
from tests.helpers import agent_client, human_client

CONFIG = PlacetConfig(agents={
    "claude": AgentSpec(tmux_session="agent-claude", token="tok-claude", allowed_targets=["human", "codex"]),
    "codex": AgentSpec(tmux_session="agent-codex", token="tok-codex", allowed_targets=["human", "claude"]),
})


@pytest.fixture
def setup(tmp_path):
    db = Database(str(tmp_path / "placet.db"))
    db.init_db()
    return create_app(db=db, config=CONFIG), db


async def event(client, session, **body):
    resp = await client.post(f"/api/sessions/{session}/events", json={"provider": "claude-code", **body})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def executing(app, db, claude):
    req = db.create_request(RequestCreate(sender_id="codex", recipient_id="claude", prompt="fai tutto"))
    async with human_client(app) as human:
        await human.post(f"/api/requests/{req.id}/decision", json={"status": "APPROVED"})
    await event(claude, "c1", event="start")
    await claude.get("/api/sessions/c1/inbox")
    await event(claude, "c1", event="prompt", request_id=req.id)
    assert db.get_request(req.id).status == RequestStatus.EXECUTING
    return req


def test_the_hook_reports_a_failed_turn_with_its_error_code():
    payload = build_event({"hook_event_name": "StopFailure", "error": "rate_limit", "cwd": "/p"})
    assert payload["event"] == "failed" and payload["error"] == "rate_limit"
    odd = build_event({"hook_event_name": "StopFailure", "error": "something new <script>"})
    assert odd["event"] == "failed" and odd["error"] == "unknown"


def test_the_installer_registers_the_failure_hook_without_the_waiter():
    hooks = claude_hooks("/opt/placet/hooks")
    commands = [h["command"] for group in hooks["StopFailure"] for h in group["hooks"]]
    assert len(commands) == 1 and "claude_code.py" in commands[0]          # output is ignored: no waiter


@pytest.mark.asyncio
async def test_a_failed_turn_does_not_conclude_the_request(setup):
    app, db = setup
    async with agent_client(app, "tok-claude") as claude:
        req = await executing(app, db, claude)
        result = await event(claude, "c1", event="failed", error="rate_limit")
    assert "implicit_reply_for" not in result
    after = db.get_request(req.id)
    assert after.status == RequestStatus.EXECUTING and after.response is None
    assert after.execution == "interrupted"
    failed = [e for e in db.list_events(req.id) if e.event == "turn_failed"]
    assert failed and failed[0].details == {"error": "rate_limit"}
    session = db.get_session("c1")
    assert session.state == "idle" and session.turn_error == "rate_limit" and session.turn_error_at is not None


@pytest.mark.asyncio
async def test_the_error_goes_away_with_the_next_turn_or_start(setup):
    app, db = setup
    async with agent_client(app, "tok-claude") as claude:
        await event(claude, "c1", event="start")
        await event(claude, "c1", event="prompt")
        await event(claude, "c1", event="failed", error="overloaded")
        assert db.get_session("c1").turn_error == "overloaded"
        await event(claude, "c1", event="prompt")
        assert db.get_session("c1").turn_error is None
        await event(claude, "c1", event="failed", error="rate_limit")
        await event(claude, "c1", event="start")
        assert db.get_session("c1").turn_error is None
    async with human_client(app) as human:
        listed = {s["id"]: s for s in (await human.get("/api/sessions")).json()}
    assert "turn_error" in listed["c1"]
