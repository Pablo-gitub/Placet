"""Part B: put an undelivered prompt back in the queue of the very same session ("Riprova consegna")."""
import pytest

from backend.app.models import RequestStatus
from tests.helpers import agent_client, human_client
from tests.test_undelivered import age, approve, cancel, env, forward, listing, send, start, world  # noqa: F401


async def retry(app, request_id):
    async with human_client(app) as human:
        return await human.post(f"/api/requests/{request_id}/undelivered/retry")


async def blocked(app, db):
    await world(app)
    rid = await send(app)
    await approve(app, rid)
    age(db, rid, minutes=10)
    assert (await listing(app))[rid]["cause"] == "blocked"
    return rid


@pytest.mark.asyncio
async def test_a_blocked_prompt_goes_back_in_the_queue_of_the_same_session(env):
    app, db = env
    rid = await blocked(app, db)
    before = db.get_request(rid)
    resp = await retry(app, rid)
    assert resp.status_code == 200 and resp.json()["status"] == "APPROVED"
    after = db.get_request(rid)
    assert after.recipient_session_id == before.recipient_session_id == "gm-1"                 # the very same session
    assert after.delivery == "waiting" and after.delivery_attempts == 0
    assert after.updated_at > before.updated_at
    assert any(e.event == "undelivered_retried" and e.actor.startswith("human:") for e in db.list_events(rid))
    assert rid not in await listing(app)                                                       # on its way again, not stuck
    assert db.claim_injection(rid)                                                             # and the bridge can take it


@pytest.mark.asyncio
async def test_a_failed_delivery_can_be_tried_again(env):
    app, db = env
    rid = await blocked(app, db)
    db.claim_injection(rid)
    with db._get_connection() as conn:
        conn.execute("UPDATE requests SET status = 'DELIVERY_FAILED', delivery_state = 'failed', delivery_attempts = 3 WHERE id = ?", (rid,))
    item = (await listing(app))[rid]
    assert item["cause"] == "blocked" and item["can_retry"] is True
    assert item["can_cancel"] is False and item["can_forward"] is False
    assert (await retry(app, rid)).status_code == 200
    after = db.get_request(rid)
    assert (after.status, after.delivery, after.delivery_attempts) == (RequestStatus.APPROVED, "waiting", 0)
    assert after.expires_at > after.updated_at                                                  # not left to expire at once


@pytest.mark.asyncio
async def test_the_listing_offers_retry_only_where_it_means_something(env):
    app, db = env
    await world(app)
    waiting_approval = await send(app)
    stuck = await send(app)
    await approve(app, stuck)
    age(db, stuck, minutes=10)
    items = await listing(app)
    assert items[stuck]["can_retry"] is True and items[waiting_approval]["can_retry"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["delivering", "executing", "completed", "cancelled", "pending"])
async def test_it_is_refused_once_delivery_is_running_or_over(env, state):
    app, db = env
    await world(app)
    rid = await send(app)
    if state != "pending":
        await approve(app, rid)
    if state in ("delivering", "executing", "completed"):
        assert db.claim_injection(rid)
    if state in ("executing", "completed"):
        db.confirm_injection(rid, actor="bridge", via="test")
    if state == "completed":
        async with agent_client(app, "tok-gemini") as client:
            assert (await client.post(f"/api/requests/{rid}/reply", json={"content": "done"})).status_code == 200
    if state == "cancelled":
        assert (await cancel(app, rid)).status_code == 200
    before = db.get_request(rid)
    resp = await retry(app, rid)
    assert resp.status_code == 409 and resp.json()["code"] == "undelivered.not_available"
    assert db.get_request(rid).status == before.status and db.get_request(rid).delivery_attempts == before.delivery_attempts


@pytest.mark.asyncio
async def test_retry_and_the_delivery_claim_cannot_both_win(env):
    app, db = env
    rid = await blocked(app, db)
    assert db.claim_injection(rid)                                                              # the bridge got there first
    assert (await retry(app, rid)).status_code == 409
    assert db.get_request(rid).status == RequestStatus.DELIVERING


@pytest.mark.asyncio
async def test_only_the_human_retries_and_a_question_or_a_missing_request_is_refused(env):
    app, db = env
    rid = await blocked(app, db)
    async with agent_client(app, "tok-codex") as client:
        assert (await client.post(f"/api/requests/{rid}/undelivered/retry")).status_code == 403
    async with agent_client(app, "tok-codex") as client:
        question = (await client.post("/api/requests", json={"recipient_id": "human", "prompt": "ok?"})).json()["id"]
    assert (await retry(app, question)).status_code == 409
    assert (await retry(app, 9999)).status_code == 404
