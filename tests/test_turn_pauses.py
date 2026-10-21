"""RFC-16: a paused turn (Antigravity background task or plan review) does not
conclude the Placet request; the resumed turn belongs to the same request."""
import pytest

from backend.app.config import AgentSpec, PlacetConfig
from backend.app.database import Database
from backend.app.main import create_app
from backend.app.models import RequestCreate, RequestStatus
from tests.helpers import agent_client, human_client

CONFIG = PlacetConfig(agents={
    "claude": AgentSpec(tmux_session="agent-claude", token="tok-claude", allowed_targets=["human", "gemini"]),
    "gemini": AgentSpec(tmux_session="agent-gemini", token="tok-gemini", allowed_targets=["human", "claude"]),
})


@pytest.fixture
def setup(tmp_path):
    db = Database(str(tmp_path / "placet.db"))
    db.init_db()
    return create_app(db=db, config=CONFIG), db


async def event(client, session, **body):
    resp = await client.post(f"/api/sessions/{session}/events", json={"provider": "antigravity", **body})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def executing(app, db, gemini):
    req = db.create_request(RequestCreate(sender_id="claude", recipient_id="gemini", prompt="fai tutto"))
    async with human_client(app) as human:
        await human.post(f"/api/requests/{req.id}/decision", json={"status": "APPROVED"})
    await event(gemini, "g1", event="start")
    await gemini.get("/api/sessions/g1/inbox")
    await event(gemini, "g1", event="prompt", request_id=req.id)
    assert db.get_request(req.id).status == RequestStatus.EXECUTING
    return req


@pytest.mark.asyncio
async def test_a_background_pause_does_not_conclude_and_the_resume_continues(setup):
    app, db = setup
    async with agent_client(app, "tok-gemini") as gemini:
        req = await executing(app, db, gemini)
        paused = await event(gemini, "g1", event="stop", last_message="In corso i test.", pause="background_task")
        assert "implicit_reply_for" not in paused
        assert db.get_request(req.id).status == RequestStatus.EXECUTING
        session = db.get_session("g1")
        assert session.state == "busy" and session.turn_request_id == req.id
        resumed = await event(gemini, "g1", event="prompt", resumes=True)
        assert resumed["turn_request_id"] == req.id
        done = await event(gemini, "g1", event="stop", last_message="Fatto tutto.")
    assert done["implicit_reply_for"] == req.id
    assert db.get_request(req.id).response.content == "Fatto tutto."
    assert "turn_paused" in [e.event for e in db.list_events(req.id)]


@pytest.mark.asyncio
async def test_a_plan_review_pause_notifies_the_user(setup):
    app, db = setup
    async with agent_client(app, "tok-gemini") as gemini:
        req = await executing(app, db, gemini)
        await event(gemini, "g1", event="stop", last_message="Ecco il piano.", pause="app_review")
    assert db.get_request(req.id).status == RequestStatus.EXECUTING
    assert ("app_review", ) == tuple(e.details.get("reason") for e in db.list_events(req.id) if e.event == "turn_paused")


@pytest.mark.asyncio
async def test_resume_without_a_paused_request_is_an_ordinary_turn(setup):
    app, db = setup
    async with agent_client(app, "tok-gemini") as gemini:
        await event(gemini, "g1", event="start")
        resumed = await event(gemini, "g1", event="prompt", resumes=True)
        assert resumed["turn_request_id"] is None
        stop = await event(gemini, "g1", event="stop", last_message="ok")
    assert "implicit_reply_for" not in stop
