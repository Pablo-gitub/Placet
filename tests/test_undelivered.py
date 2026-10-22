"""Messages that have not reached their recipient: list them by cause, cancel them or send them elsewhere."""
from datetime import datetime, timedelta, timezone

import pytest

from backend.app import envelopes
from backend.app.config import AgentSpec, PlacetConfig
from backend.app.database import Database
from backend.app.main import create_app
from tests.helpers import agent_client, human_client

CONFIG = PlacetConfig(agents={
    "claude": AgentSpec(tmux_session="agent-claude", token="tok-claude", allowed_targets=["human", "codex", "gemini"]),
    "codex": AgentSpec(tmux_session="agent-codex", token="tok-codex", allowed_targets=["human", "claude", "gemini"]),
    "gemini": AgentSpec(tmux_session="agent-gemini", token="tok-gemini", allowed_targets=["human", "claude", "codex"]),
})


@pytest.fixture
def env(tmp_path):
    db = Database(str(tmp_path / "placet.db"))
    db.init_db()
    return create_app(db=db, config=CONFIG, bridge_token="tok-bridge"), db


async def start(app, agent, session, provider, busy=False):
    async with agent_client(app, f"tok-{agent}") as client:
        await client.post(f"/api/sessions/{session}/events", json={"event": "start", "provider": provider, "cwd": "/p"})
        if busy:
            await client.post(f"/api/sessions/{session}/events", json={"event": "prompt", "provider": provider})


async def world(app):
    """A busy Codex sender, a Codex recipient (and a spare one), and two Gemini sessions."""
    await start(app, "codex", "cx-send", "codex", busy=True)
    await start(app, "codex", "cx-recv", "codex")
    await start(app, "codex", "cx-spare", "codex")
    await start(app, "gemini", "gm-1", "antigravity")
    await start(app, "gemini", "gm-2", "antigravity")


async def send(app, session="gm-1", target="gemini", agent="codex"):
    async with agent_client(app, f"tok-{agent}") as client:
        resp = await client.post("/api/requests", json={"recipient_id": target, "prompt": "please do it",
                                                        "recipient_session_id": session})
        assert resp.status_code == 201, resp.text
        return resp.json()["id"]


async def approve(app, request_id):
    async with human_client(app) as human:
        assert (await human.post(f"/api/requests/{request_id}/decision", json={"status": "APPROVED"})).status_code == 200


async def cancel(app, request_id):
    async with human_client(app) as human:
        return await human.post(f"/api/requests/{request_id}/undelivered/cancel")


async def forward(app, request_id, session):
    async with human_client(app) as human:
        return await human.post(f"/api/requests/{request_id}/undelivered/forward", json={"session_id": session})


async def listing(app):
    async with human_client(app) as human:
        resp = await human.get("/api/requests/undelivered")
        assert resp.status_code == 200, resp.text
        return {item["request"]["id"]: item for item in resp.json()}


def age(db, request_id, minutes):
    """Pretend the request was approved `minutes` ago."""
    then = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    with db._get_connection() as conn:
        conn.execute("UPDATE requests SET updated_at = ?, decided_at = ? WHERE id = ?", (then, then, request_id))


# --- cancel --------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_pending_prompt_is_cancelled_and_the_sender_is_told(env):
    app, db = env
    await world(app)
    rid = await send(app)
    resp = await cancel(app, rid)
    assert resp.status_code == 200 and resp.json()["status"] == "CANCELLED"
    events = [(e.event, e.actor) for e in db.list_events(rid)]
    assert any(event == "cancelled" and actor.startswith("human:") for event, actor in events)
    req = db.get_request(rid)
    assert req.delivered_at is None                                           # the sender still has to be told
    assert "cancelled" in envelopes.outcome(req, language="en").lower()


@pytest.mark.asyncio
async def test_an_approved_scheduled_prompt_can_be_cancelled(env):
    app, db = env
    await world(app)
    rid = await send(app)
    await approve(app, rid)
    db.schedule_request(rid, datetime.now(timezone.utc) + timedelta(hours=3), actor="system:relation#8")
    assert (await cancel(app, rid)).json()["status"] == "CANCELLED"
    assert db.get_request(rid).delivery == "cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize("claimed", ["delivering", "executing", "completed"])
async def test_once_delivery_started_cancel_and_forward_are_refused(env, claimed):
    app, db = env
    await world(app)
    rid = await send(app)
    await approve(app, rid)
    assert db.claim_injection(rid)
    if claimed in ("executing", "completed"):
        db.confirm_injection(rid, actor="bridge", via="test")
    if claimed == "completed":
        async with agent_client(app, "tok-gemini") as client:
            assert (await client.post(f"/api/requests/{rid}/reply", json={"content": "done"})).status_code == 200
    before = db.get_request(rid).status
    for resp in (await cancel(app, rid), await forward(app, rid, "gm-2")):
        assert resp.status_code == 409
        assert resp.json()["code"] == "undelivered.not_available"
    assert db.get_request(rid).status == before


@pytest.mark.asyncio
async def test_cancel_and_the_delivery_claim_cannot_both_win(env):
    app, db = env
    await world(app)
    rid = await send(app)
    await approve(app, rid)
    assert (await cancel(app, rid)).status_code == 200
    assert not db.claim_injection(rid)                                        # nothing left to deliver
    rid2 = await send(app)
    await approve(app, rid2)
    assert db.claim_injection(rid2)                                           # delivery won the race
    assert (await cancel(app, rid2)).status_code == 409
    assert db.get_request(rid2).status.value == "DELIVERING"


@pytest.mark.asyncio
async def test_only_the_user_can_cancel_or_forward(env):
    app, db = env
    await world(app)
    rid = await send(app)
    async with agent_client(app, "tok-codex") as client:
        assert (await client.post(f"/api/requests/{rid}/undelivered/cancel")).status_code == 403
        assert (await client.post(f"/api/requests/{rid}/undelivered/forward", json={"session_id": "gm-2"})).status_code == 403
        assert (await client.get("/api/requests/undelivered")).status_code == 403
    assert db.get_request(rid).status.value == "PENDING"


@pytest.mark.asyncio
async def test_cancelling_twice_or_a_missing_request_is_a_product_error(env):
    app, db = env
    await world(app)
    rid = await send(app)
    assert (await cancel(app, rid)).status_code == 200
    assert (await cancel(app, rid)).status_code == 409
    assert (await cancel(app, 9999)).status_code == 404


# --- forward -------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_pending_prompt_goes_to_another_session_of_the_same_agent(env):
    app, db = env
    await world(app)
    rid = await send(app)
    resp = await forward(app, rid, "gm-2")
    assert resp.status_code == 200, resp.text
    req = db.get_request(rid)
    assert (req.recipient_id, req.recipient_session_id, req.status.value) == ("gemini", "gm-2", "PENDING")
    assert any(e.event == "redirected" for e in db.list_events(rid))


@pytest.mark.asyncio
async def test_an_approved_waiting_prompt_can_go_to_another_agent(env):
    app, db = env
    await world(app)
    rid = await send(app)
    await approve(app, rid)
    resp = await forward(app, rid, "cx-spare")
    assert resp.status_code == 200, resp.text
    req = db.get_request(rid)
    assert (req.recipient_id, req.recipient_session_id, req.status.value) == ("codex", "cx-spare", "APPROVED")
    assert req.delivery == "waiting"
    assert db.claim_injection(rid)                                            # and it is delivered to the new session


@pytest.mark.asyncio
async def test_forward_refuses_the_senders_own_session_the_same_session_and_closed_ones(env):
    app, db = env
    await world(app)
    rid = await send(app)
    assert (await forward(app, rid, "cx-send")).status_code == 422             # the sender's own session
    assert (await forward(app, rid, "gm-1")).status_code == 422                # where it already is
    assert (await forward(app, rid, "nobody")).status_code == 422
    db.end_session("gm-2")
    assert (await forward(app, rid, "gm-2")).status_code == 422
    assert db.get_request(rid).recipient_session_id == "gm-1"


@pytest.mark.asyncio
async def test_a_question_to_the_user_cannot_be_forwarded(env):
    app, db = env
    await world(app)
    async with agent_client(app, "tok-codex") as client:
        rid = (await client.post("/api/requests", json={"recipient_id": "human", "prompt": "ok?"})).json()["id"]
    assert (await forward(app, rid, "gm-2")).status_code == 409


# --- the listing -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_each_undelivered_prompt_is_listed_under_its_cause(env):
    app, db = env
    await world(app)
    waiting_approval = await send(app)
    refresh = await send(app)
    await approve(app, refresh)
    db.schedule_request(refresh, datetime.now(timezone.utc) + timedelta(hours=2), actor="system:relation#8")
    mine = await send(app)
    await approve(app, mine)
    db.schedule_request(mine, datetime.now(timezone.utc) + timedelta(hours=1), actor="human:local")
    stuck = await send(app)
    await approve(app, stuck)
    age(db, stuck, minutes=10)
    items = await listing(app)
    assert items[waiting_approval]["cause"] == "awaiting_approval"
    assert items[refresh]["cause"] == "awaiting_refresh" and items[refresh]["due_at"]
    assert items[mine]["cause"] == "scheduled" and items[mine]["due_at"]
    assert items[stuck]["cause"] == "blocked"
    for item in items.values():
        assert item["can_cancel"] is True and item["can_forward"] is True


@pytest.mark.asyncio
async def test_delivered_cancelled_and_user_questions_are_not_listed(env):
    app, db = env
    await world(app)
    delivering = await send(app)
    await approve(app, delivering)
    db.claim_injection(delivering)
    cancelled = await send(app)
    await cancel(app, cancelled)
    async with agent_client(app, "tok-codex") as client:
        question = (await client.post("/api/requests", json={"recipient_id": "human", "prompt": "ok?"})).json()["id"]
    items = await listing(app)
    assert delivering not in items and cancelled not in items and question not in items


@pytest.mark.asyncio
async def test_a_prompt_to_an_unreachable_session_is_blocked_at_once(env):
    app, db = env
    await world(app)
    await start(app, "claude", "cl-idle", "claude-code")                      # no waiter: cannot be reached now
    rid = await send(app, session="cl-idle", target="claude")
    await approve(app, rid)
    assert (await listing(app))[rid]["cause"] == "blocked"


@pytest.mark.asyncio
async def test_the_listing_names_the_nodes_and_the_project(env):
    app, db = env
    await world(app)
    sender, recipient = db.create_node("planner", "cx-send"), db.create_node("reviewer", "gm-1")
    rid = await send(app)
    item = (await listing(app))[rid]
    assert (item["sender_node"], item["recipient_node"]) == ("planner", "reviewer")
    assert item["request"]["sender_id"] == "codex" and item["request"]["recipient_id"] == "gemini"
