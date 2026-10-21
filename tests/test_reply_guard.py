"""RFC-16: a reply goes through placet_reply. While B runs a request from A,
B cannot open a new request to A; progress updates do not close the request."""
import pytest

from backend.app.config import AgentSpec, PlacetConfig
from backend.app.database import Database
from backend.app.main import create_app
from backend.app.models import RequestStatus
from tests.helpers import agent_client, human_client

CONFIG = PlacetConfig(agents={
    "claude": AgentSpec(tmux_session="agent-claude", token="tok-claude", allowed_targets=["human", "gemini"]),
    "gemini": AgentSpec(tmux_session="agent-gemini", token="tok-gemini", allowed_targets=["human", "claude"]),
})


@pytest.fixture
def env(tmp_path):
    db = Database(str(tmp_path / "placet.db"))
    db.init_db()
    return create_app(db=db, config=CONFIG), db


async def event(client, session, **body):
    resp = await client.post(f"/api/sessions/{session}/events", json=body)
    assert resp.status_code == 200, resp.text


async def team(app, db):
    """Claude (node lead) runs; Gemini (node dev) executes a request from it."""
    async with agent_client(app, "tok-claude") as claude, agent_client(app, "tok-gemini") as gemini:
        await event(claude, "cl", event="start", provider="claude-code", cwd="/p")
        await event(gemini, "g1", event="start", provider="antigravity", cwd="/p")
        lead, dev = db.create_node("lead", "cl"), db.create_node("dev", "g1")
        work = db.create_relation_type("Developer", "")
        back = db.create_relation_type("Reviewer", "")
        db.create_relation(lead.id, dev.id, work.id, "automatic")
        db.create_relation(dev.id, lead.id, back.id, "automatic")
        resp = await claude.post("/api/requests", json={
            "target_node": "dev", "prompt": "implementa", "sender_session_id": "cl", "rule_acknowledged": True})
        assert resp.status_code == 201, resp.text
        req = resp.json()
        await gemini.get("/api/sessions/g1/inbox")
        await event(gemini, "g1", event="prompt", provider="antigravity", request_id=req["id"])
    assert db.get_request(req["id"]).status == RequestStatus.EXECUTING
    return req


@pytest.mark.asyncio
async def test_a_new_request_back_to_the_sender_is_refused_while_running(env):
    app, db = env
    req = await team(app, db)
    async with agent_client(app, "tok-gemini") as gemini:
        resp = await gemini.post("/api/requests", json={
            "target_node": "lead", "prompt": "resoconto finale", "sender_session_id": "g1", "rule_acknowledged": True})
        assert resp.status_code == 409
        body = resp.json()
        assert body["code"] == "request.reply_expected" and str(req["id"]) in body["detail"]
        # An ad hoc request to the same agent is refused as well.
        resp = await gemini.post("/api/requests", json={
            "recipient_id": "claude", "prompt": "resoconto", "sender_session_id": "g1"})
        assert resp.status_code == 409
        # Questions to the human stay possible.
        resp = await gemini.post("/api/requests", json={
            "recipient_id": "human", "prompt": "Quale opzione?", "sender_session_id": "g1"})
        assert resp.status_code == 201
        # Once replied, a new request is allowed again.
        assert (await gemini.post(f"/api/requests/{req['id']}/reply", json={"content": "fatto"})).status_code == 200
        resp = await gemini.post("/api/requests", json={
            "target_node": "lead", "prompt": "nuovo tema", "sender_session_id": "g1", "rule_acknowledged": True})
        assert resp.status_code == 201
    assert len(db.list_requests()) == 3


@pytest.mark.asyncio
async def test_progress_updates_keep_the_request_open(env):
    app, db = env
    req = await team(app, db)
    async with agent_client(app, "tok-gemini") as gemini:
        resp = await gemini.post(f"/api/requests/{req['id']}/progress", json={"content": "Parte 1 di 4 fatta"})
        assert resp.status_code == 200
        out = resp.json()
        assert out["status"] == "EXECUTING" and out["progress_text"] == "Parte 1 di 4 fatta"
        assert out["progress_at"] is not None
    async with agent_client(app, "tok-claude") as claude:
        # The sender reads it with placet_check_status; it cannot post one.
        seen = (await claude.get(f"/api/requests/{req['id']}")).json()
        assert seen["progress_text"] == "Parte 1 di 4 fatta"
        assert (await claude.post(f"/api/requests/{req['id']}/progress", json={"content": "x"})).status_code == 403
    async with human_client(app) as human:
        assert (await human.get("/api/requests")).json()[0]["progress_text"] == "Parte 1 di 4 fatta"
    assert "progress" in [e.event for e in db.list_events(req["id"])]
    async with agent_client(app, "tok-gemini") as gemini:
        await gemini.post(f"/api/requests/{req['id']}/reply", json={"content": "fatto"})
        late = await gemini.post(f"/api/requests/{req['id']}/progress", json={"content": "ancora"})
        assert late.status_code == 409
