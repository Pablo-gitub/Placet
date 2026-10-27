"""A question to the user opened during a Placet request's turn and still PENDING at the end of the turn does not
conclude the request (case #535): the user's answer resumes the same task, and the next turn concludes it."""
import pytest

from backend.app.config import AgentSpec, PlacetConfig
from backend.app.database import Database
from backend.app.main import create_app
from backend.app.models import RequestCreate, RequestStatus
from tests.helpers import agent_client, human_client

CONFIG = PlacetConfig(agents={
    "claude": AgentSpec(tmux_session="agent-claude", token="tok-claude", allowed_targets=["human", "gemini", "codex"]),
    "gemini": AgentSpec(tmux_session="agent-gemini", token="tok-gemini", allowed_targets=["human", "claude"]),
    "codex": AgentSpec(tmux_session="agent-codex", token="tok-codex", allowed_targets=["human", "claude"]),
})
PROVIDERS = {"gemini": "antigravity", "codex": "codex"}


@pytest.fixture
def setup(tmp_path):
    db = Database(str(tmp_path / "placet.db"))
    db.init_db()
    return create_app(db=db, config=CONFIG), db


async def event(client, agent, session, **body):
    resp = await client.post(f"/api/sessions/{session}/events", json={"provider": PROVIDERS[agent], **body})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def executing(app, db, agent_client_, agent="gemini", session="g1"):
    req = db.create_request(RequestCreate(sender_id="claude", recipient_id=agent, prompt="fai tutto"))
    async with human_client(app) as human:
        await human.post(f"/api/requests/{req.id}/decision", json={"status": "APPROVED"})
    await event(agent_client_, agent, session, event="start")
    await agent_client_.get(f"/api/sessions/{session}/inbox")
    await event(agent_client_, agent, session, event="prompt", request_id=req.id)
    assert db.get_request(req.id).status == RequestStatus.EXECUTING
    return req


async def ask(client, text="Quale approccio?"):
    resp = await client.post("/api/requests", json={"recipient_id": "human", "prompt": text, "question_type": "confirmation"})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def answer(app, question_id):
    async with human_client(app) as human:
        resp = await human.post(f"/api/requests/{question_id}/decision", json={"status": "APPROVED"})
        assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize("agent", ["gemini", "codex"])
async def test_a_pending_question_at_the_end_of_the_turn_does_not_conclude_the_request(setup, agent):
    app, db = setup
    session = "g1" if agent == "gemini" else "c1"
    async with agent_client(app, f"tok-{agent}") as client:
        req = await executing(app, db, client, agent, session)
        question = await ask(client)
        stopped = await event(client, agent, session, event="stop", last_message="Aspetto la tua risposta.")
        assert "implicit_reply_for" not in stopped
        kept = db.get_request(req.id)
        assert kept.status == RequestStatus.EXECUTING and kept.response is None
        assert db.get_session(session).turn_request_id == req.id and db.get_session(session).state == "idle"
        assert "turn_waiting_user" in [e.event for e in db.list_events(req.id)]
        assert db.get_request(question).status == RequestStatus.PENDING


@pytest.mark.asyncio
async def test_the_answer_resumes_the_same_task_and_the_next_turn_concludes_it(setup):
    app, db = setup
    async with agent_client(app, "tok-gemini") as gemini:
        req = await executing(app, db, gemini)
        question = await ask(gemini)
        await event(gemini, "gemini", "g1", event="stop", last_message="Aspetto la tua risposta.")
        await answer(app, question)
        resumed = await event(gemini, "gemini", "g1", event="prompt")                       # the answer reaches the agent
        assert resumed["turn_request_id"] == req.id
        done = await event(gemini, "gemini", "g1", event="stop", last_message="Fatto, con l'approccio B.")
    assert done["implicit_reply_for"] == req.id
    final = db.get_request(req.id)
    assert final.status == RequestStatus.COMPLETED and final.response.content == "Fatto, con l'approccio B."
    assert "turn_resumed" in [e.event for e in db.list_events(req.id)]


@pytest.mark.asyncio
async def test_without_a_pending_question_the_end_of_the_turn_is_the_reply_as_before(setup):
    app, db = setup
    async with agent_client(app, "tok-gemini") as gemini:
        req = await executing(app, db, gemini)
        done = await event(gemini, "gemini", "g1", event="stop", last_message="Fatto.")
    assert done["implicit_reply_for"] == req.id and db.get_request(req.id).status == RequestStatus.COMPLETED


@pytest.mark.asyncio
async def test_a_question_answered_before_the_end_of_the_turn_does_not_hold_the_request(setup):
    app, db = setup
    async with agent_client(app, "tok-gemini") as gemini:
        req = await executing(app, db, gemini)
        question = await ask(gemini)
        await answer(app, question)
        done = await event(gemini, "gemini", "g1", event="stop", last_message="Fatto dopo la risposta.")
    assert done["implicit_reply_for"] == req.id


@pytest.mark.asyncio
async def test_an_old_unanswered_question_does_not_block_a_later_request(setup):
    app, db = setup
    async with agent_client(app, "tok-gemini") as gemini:
        await event(gemini, "gemini", "g1", event="start")
        await event(gemini, "gemini", "g1", event="prompt")                                  # a user turn: no request
        old = await ask(gemini, "Domanda di ieri")
        await event(gemini, "gemini", "g1", event="stop", last_message="Aspetto.")
        req = await executing(app, db, gemini)                                               # a request begins afterwards
        done = await event(gemini, "gemini", "g1", event="stop", last_message="Fatto.")
    assert db.get_request(old).status == RequestStatus.PENDING
    assert done["implicit_reply_for"] == req.id


@pytest.mark.asyncio
async def test_a_question_from_another_session_of_the_agent_does_not_hold_this_request(setup):
    app, db = setup
    async with agent_client(app, "tok-gemini") as gemini:
        await event(gemini, "gemini", "g2", event="start")
        req = await executing(app, db, gemini)
        other = db.create_request(RequestCreate(sender_id="gemini", recipient_id="human", prompt="Altra sessione?",
                                                question_type="confirmation"))
        db.link_sender_session(other.id, "g2", "gemini")
        done = await event(gemini, "gemini", "g1", event="stop", last_message="Fatto.")
    assert done["implicit_reply_for"] == req.id


@pytest.mark.asyncio
async def test_the_agent_level_turn_end_follows_the_same_rule(setup):
    app, db = setup
    async with agent_client(app, "tok-gemini") as gemini:
        req = db.create_request(RequestCreate(sender_id="claude", recipient_id="gemini", prompt="fai"))
        async with human_client(app) as human:
            await human.post(f"/api/requests/{req.id}/decision", json={"status": "APPROVED"})
        assert db.claim_injection(req.id)
        await gemini.post("/api/agents/me/state", json={"state": "busy", "request_id": req.id})
        question = await ask(gemini)
        stopped = (await gemini.post("/api/agents/me/state", json={"state": "idle", "last_message": "Aspetto."})).json()
        assert "implicit_reply_for" not in stopped
        assert db.get_request(req.id).status == RequestStatus.EXECUTING
        await answer(app, question)
        resumed = (await gemini.post("/api/agents/me/state", json={"state": "busy"})).json()   # the answer, no request id
        assert resumed["turn_request_id"] == req.id
        done = (await gemini.post("/api/agents/me/state", json={"state": "idle", "last_message": "Fatto."})).json()
    assert done["implicit_reply_for"] == req.id
