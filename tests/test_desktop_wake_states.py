"""
The states of a Claude Desktop wake-up episode, kept apart: the link was handed over
(open_requested), the window for evidence is running (waiting_for_session), nothing
showed up (wake_unconfirmed), the session listens (reachable). rc=0 is never "started".
"""
from datetime import datetime, timedelta, timezone

import pytest

from tests.helpers import agent_client, human_client
from tests.test_desktop_wake_budget import SID, desktop_session, env, report, work_ids, write  # noqa: F401


def age(db, seconds, sid=SID):
    then = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
    with db._get_connection() as conn:
        conn.execute("UPDATE sessions SET wake_at = ? WHERE id = ?", (then, sid))


@pytest.mark.asyncio
async def test_an_accepted_link_goes_through_requested_waiting_and_unconfirmed(env):  # noqa: F811
    app, db, path = env
    await desktop_session(app)
    await write(app)
    assert db.get_session(SID).wake_state is None
    await report(app, "open_requested")
    assert db.get_session(SID).wake_state == "open_requested"             # just handed over
    age(db, 10)
    assert db.get_session(SID).wake_state == "waiting_for_session"        # the window for evidence is running
    age(db, 61)
    assert db.get_session(SID).wake_state == "wake_unconfirmed"           # no evidence in time
    assert db.settle_wake_episodes()["unconfirmed"] == [SID]              # persisted by the sweep, once
    assert db.settle_wake_episodes()["unconfirmed"] == []
    assert db.get_session(SID).wake_state == "wake_unconfirmed"
    assert await work_ids(app) == []                                      # and no further automatic open


@pytest.mark.asyncio
async def test_a_session_start_during_the_window_is_the_evidence(env):  # noqa: F811
    app, db, path = env
    await desktop_session(app)
    await write(app)
    await report(app, "open_requested")
    age(db, 10)
    await desktop_session(app)                                            # SessionStart from the engine
    session = db.get_session(SID)
    assert (session.wake_state, session.wake_attempts) == ("reachable", 0)


@pytest.mark.asyncio
async def test_a_waiter_heartbeat_is_evidence_even_after_the_window(env):  # noqa: F811
    app, db, path = env
    await desktop_session(app)
    await write(app)
    await report(app, "open_requested")
    age(db, 300)
    assert db.get_session(SID).wake_state == "wake_unconfirmed"
    async with agent_client(app, "tok-claude") as claude:
        await claude.get(f"/api/sessions/{SID}/inbox")                    # the waiter is listening now
    assert db.get_session(SID).wake_state == "reachable"


@pytest.mark.asyncio
async def test_the_episode_ends_when_the_pending_mail_is_gone(env):  # noqa: F811
    app, db, path = env
    await desktop_session(app)
    req_id = await write(app)
    await report(app, "open_requested")
    age(db, 300)
    db.settle_wake_episodes()
    assert db.get_session(SID).wake_state == "wake_unconfirmed"
    with db._get_connection() as conn:
        conn.execute("UPDATE requests SET status = 'CANCELLED' WHERE id = ?", (req_id,))
    db.settle_wake_episodes()
    session = db.get_session(SID)
    assert (session.wake_state, session.wake_attempts) == (None, 0)
    await write(app, "un nuovo messaggio")                                # a new episode: one automatic open again
    assert await work_ids(app) == [SID]


@pytest.mark.asyncio
async def test_a_waiting_window_is_not_cut_short_by_the_sweep(env):  # noqa: F811
    app, db, path = env
    await desktop_session(app)
    await write(app)
    await report(app, "open_requested")
    age(db, 20)
    assert db.settle_wake_episodes()["unconfirmed"] == []
    assert db.get_session(SID).wake_state == "waiting_for_session"


@pytest.mark.asyncio
async def test_two_failures_are_a_failed_wake_not_an_unconfirmed_one(env):  # noqa: F811
    app, db, path = env
    await desktop_session(app)
    await write(app)
    await report(app, "failed")
    await report(app, "failed")
    assert db.get_session(SID).wake_state == "wake_failed"


@pytest.mark.asyncio
async def test_the_states_are_listed_for_the_pwa(env):  # noqa: F811
    app, db, path = env
    await desktop_session(app)
    await write(app)
    await report(app, "open_requested")
    async with human_client(app) as human:
        listed = {s["id"]: s for s in (await human.get("/api/sessions")).json()}
    assert listed[SID]["wake_state"] == "open_requested" and listed[SID]["wake_at"] is not None
