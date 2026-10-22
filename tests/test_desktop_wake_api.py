"""The user's explicit "retry the opening" of a Claude Desktop session, and the one push per episode."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.config import AgentSpec, PlacetConfig
from backend.app.database import Database
from backend.app.main import create_app
from backend.app.models import RequestStatus
from backend.app.webpush import WebPush
from tests.helpers import agent_client, human_client
from tests.test_desktop_wake_budget import CONFIG, SID, desktop_session, report, work_ids, write
from tests.test_desktop_wake_states import age


@pytest.fixture
def env(tmp_path):
    db = Database(str(tmp_path / "placet.db"))
    db.init_db()
    sent = []

    def fake_send(sub, payload, urgency):
        sent.append(json.loads(payload))
        return 201

    app = create_app(db=db, config=CONFIG, bridge_token="tok-bridge",
                     web_push=WebPush(db, subject="https://host.example", send=fake_send))
    return app, db, sent


async def subscribe(app):
    sub = {"endpoint": "https://web.push.apple.com/QGx1", "keys": {"p256dh": "BPk", "auth": "au"}}
    async with human_client(app) as human:
        await human.post("/api/push/subscription", json=sub)


async def retry(app, sid=SID):
    async with human_client(app) as human:
        return await human.post(f"/api/sessions/{sid}/wake")


async def unconfirmed(app, db):
    await desktop_session(app)
    req_id = await write(app)
    await report(app, "open_requested")
    age(db, 120)
    return req_id


@pytest.mark.asyncio
async def test_retry_starts_a_new_episode_that_skips_the_bridge_cooldown(env):
    app, db, _ = env
    await unconfirmed(app, db)
    assert await work_ids(app) == []
    resp = await retry(app)
    assert resp.status_code == 200
    session = db.get_session(SID)
    assert (session.wake_state, session.wake_attempts) == (None, 0)
    async with agent_client(app, "tok-bridge") as bridge:
        [wake] = (await bridge.get("/api/bridge/work")).json()["claude_wake_sessions"]
    assert wake["id"] == SID and wake["force"] is True                 # the bridge may ignore its own cooldown
    await report(app, "open_requested")                                # the new episode used its open
    assert await work_ids(app) == []
    assert (await retry(app)).status_code == 409                       # the window is running


@pytest.mark.asyncio
async def test_retry_while_the_window_runs_or_without_a_reason_is_refused(env):
    app, db, _ = env
    await desktop_session(app)
    assert (await retry(app)).json()["code"] == "session.nothing_to_wake"
    await write(app)
    await report(app, "open_requested")
    busy = await retry(app)
    assert busy.status_code == 409 and busy.json()["code"] == "session.wake_in_progress"
    await desktop_session(app, "cli", entrypoint="cli")
    assert (await retry(app, "cli")).json()["code"] == "session.wake_unsupported"
    assert (await retry(app, "nobody")).status_code == 404


@pytest.mark.asyncio
async def test_retry_is_for_the_human_and_not_for_a_reachable_session(env):
    app, db, _ = env
    await unconfirmed(app, db)
    async with agent_client(app, "tok-claude") as claude:
        assert (await claude.post(f"/api/sessions/{SID}/wake")).status_code == 403
    async with agent_client(app, "tok-bridge") as bridge:
        assert (await bridge.post(f"/api/sessions/{SID}/wake")).status_code == 403
    db.touch_waiter(SID)
    assert (await retry(app)).json()["code"] == "session.already_reachable"


@pytest.mark.asyncio
async def test_one_needs_you_push_per_episode_and_never_repeated(env):
    app, db, sent = env
    await subscribe(app)
    await unconfirmed(app, db)
    for _ in range(3):                                                 # the sweep runs on every request
        async with human_client(app) as human:
            await human.get("/api/requests")
        await asyncio.sleep(0.05)
    pushes = [p for p in sent if p["tag"].startswith("wake-")]
    assert len(pushes) == 1 and pushes[0]["url"] == f"/#/sessione/{SID}"
    assert "non" in pushes[0]["body"].lower() or "not" in pushes[0]["body"].lower()


@pytest.mark.asyncio
async def test_no_push_when_the_user_switched_needs_you_off_and_it_is_not_consumed(env):
    app, db, sent = env
    await subscribe(app)
    prefs = db.get_notification_preferences()
    db.set_notification_preferences({**prefs, "needs_you": False})
    await unconfirmed(app, db)
    async with human_client(app) as human:
        await human.get("/api/requests")
    await asyncio.sleep(0.05)
    assert [p for p in sent if p["tag"].startswith("wake-")] == []
    db.set_notification_preferences({**prefs, "needs_you": True})
    async with human_client(app) as human:
        await human.get("/api/requests")
    await asyncio.sleep(0.05)
    assert len([p for p in sent if p["tag"].startswith("wake-")]) == 1


@pytest.mark.asyncio
async def test_a_failed_wake_pushes_once_too_and_the_message_is_untouched(env):
    app, db, sent = env
    await subscribe(app)
    await desktop_session(app)
    req_id = await write(app)
    await report(app, "failed")
    await report(app, "failed")
    await asyncio.sleep(0.05)
    assert len([p for p in sent if p["tag"].startswith("wake-")]) == 1
    request = db.get_request(req_id)
    assert request.status == RequestStatus.APPROVED and request.delivery == "waiting"   # not failed, not delivered
    assert [e.event for e in db.list_events(req_id)] == ["created", "decided"]
