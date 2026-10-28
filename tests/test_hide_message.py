"""A message that never went out (expired, cancelled, rejected) can be hidden from the chat by the user."""
import pytest

from tests.helpers import agent_client, human_client
from tests.test_session_messages import setup, start, write  # noqa: F401


async def hide(app, request_id):
    async with human_client(app) as human:
        return await human.post(f"/api/requests/{request_id}/hide")


def end_as(db, request_id, status):
    with db._get_connection() as conn:
        conn.execute("UPDATE requests SET status = ? WHERE id = ?", (status, request_id))


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["EXPIRED", "CANCELLED", "REJECTED"])
async def test_a_message_that_never_went_out_can_be_hidden(setup, status):
    app, db = setup
    await start(app, "gemini", "g1", "antigravity")
    rid = (await write(app, "g1", "Ciao")).json()["id"]
    end_as(db, rid, status)
    resp = await hide(app, rid)
    assert resp.status_code == 200 and resp.json()["hidden_at"]
    stored = db.get_request(rid)
    assert stored.hidden_at is not None and stored.status.value == status           # not deleted: it stays in the history
    async with human_client(app) as human:
        listed = {r["id"]: r for r in (await human.get("/api/requests")).json()}
    assert listed[rid]["hidden_at"]                                                    # the PWA sees it on every device
    assert any(e.event == "hidden" and e.actor.startswith("human:") for e in db.list_events(rid))


@pytest.mark.asyncio
async def test_hiding_twice_changes_nothing(setup):
    app, db = setup
    await start(app, "gemini", "g1", "antigravity")
    rid = (await write(app, "g1")).json()["id"]
    end_as(db, rid, "EXPIRED")
    first = (await hide(app, rid)).json()["hidden_at"]
    second = await hide(app, rid)
    assert second.status_code == 200 and second.json()["hidden_at"] == first
    assert len([e for e in db.list_events(rid) if e.event == "hidden"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["PENDING", "APPROVED", "DELIVERING", "EXECUTING", "COMPLETED", "DELIVERY_FAILED"])
async def test_a_request_that_is_still_alive_failed_or_was_delivered_is_never_hidden(setup, status):
    app, db = setup
    await start(app, "gemini", "g1", "antigravity")
    rid = (await write(app, "g1")).json()["id"]
    end_as(db, rid, status)
    resp = await hide(app, rid)
    assert resp.status_code == 409 and resp.json()["code"] == "request.hide_not_allowed"
    assert db.get_request(rid).hidden_at is None


@pytest.mark.asyncio
async def test_only_the_human_hides_and_only_a_message_of_theirs(setup):
    app, db = setup
    await start(app, "gemini", "g1", "antigravity")
    rid = (await write(app, "g1")).json()["id"]
    end_as(db, rid, "EXPIRED")
    async with agent_client(app, "tok-gemini") as agent:
        assert (await agent.post(f"/api/requests/{rid}/hide")).status_code == 403
    assert db.get_request(rid).hidden_at is None
    assert (await hide(app, 9999)).status_code == 404
    async with agent_client(app, "tok-claude") as claude:
        other = (await claude.post("/api/requests", json={"recipient_id": "human", "prompt": "ok?"})).json()["id"]
    end_as(db, other, "EXPIRED")
    assert (await hide(app, other)).status_code == 409                                  # an agent's question is not a chat bubble
