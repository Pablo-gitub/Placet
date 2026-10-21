"""
A session queue held by a request whose turn end Placet never saw must not
stay unnoticed: a push once the hold has lasted a while with messages waiting,
a badge in the Home list, a release on observed evidence only.
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.database import Database
from backend.app.main import create_app
from backend.app.webpush import WebPush
from tests.helpers import agent_client, human_client
from tests.test_request_aspects import bridge_delivers
from tests.test_session_messages import CONFIG, start, write


@pytest.fixture
def env(tmp_path):
    db = Database(str(tmp_path / "placet.db"))
    db.init_db()
    sent = []

    def fake_send(sub, payload, urgency):
        sent.append(json.loads(payload))
        return 201

    push = WebPush(db, subject="https://host.example", send=fake_send)
    app = create_app(db=db, config=CONFIG, bridge_token="tok-bridge", web_push=push)
    return app, db, sent


def age_hold(db, req_id, seconds):
    then = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
    with db._get_connection() as conn:
        conn.execute("UPDATE requests SET delivery_at = ?, executing_since = ?, delivering_since = NULL,"
                     " start_unconfirmed_at = CASE WHEN start_unconfirmed_at IS NULL THEN NULL ELSE ? END"
                     " WHERE id = ?", (then, then, then, req_id))


async def held_with_one_waiting(app, db):
    """cx1 accepted a message whose turn is never seen ending; another waits."""
    await start(app, "codex", "cx1", "codex")
    held = (await write(app, "cx1", "lungo lavoro")).json()["id"]
    assert await bridge_delivers(app, "cx1") == held
    waiting = (await write(app, "cx1", "dopo")).json()["id"]
    return held, waiting


async def sweep(app):
    async with human_client(app) as human:
        assert (await human.get("/api/requests")).status_code == 200
    await asyncio.sleep(0.05)  # the push leaves from a worker thread


async def subscribe(app):
    sub = {"endpoint": "https://web.push.apple.com/QGx1", "keys": {"p256dh": "BPk", "auth": "au"}}
    async with human_client(app) as human:
        await human.post("/api/push/subscription", json=sub)


@pytest.mark.asyncio
async def test_a_long_hold_with_a_message_waiting_alerts_once(env):
    app, db, sent = env
    await subscribe(app)
    held, _ = await held_with_one_waiting(app, db)
    age_hold(db, held, 9 * 60)
    await sweep(app)  # the start is flagged unconfirmed here
    age_hold(db, held, 9 * 60)
    await sweep(app)
    assert [p for p in sent if p["tag"].startswith("queue-held")] == []
    age_hold(db, held, 11 * 60)
    await sweep(app)
    await sweep(app)
    alerts = [p for p in sent if p["tag"].startswith("queue-held")]
    assert len(alerts) == 1  # once per held request
    assert "1" in alerts[0]["title"] and alerts[0]["url"] == "/#/sessione/cx1"
    assert db.get_request(held).hold_alerted_at is not None
    assert "queue_hold_alerted" in [e.event for e in db.list_events(held)]


@pytest.mark.asyncio
async def test_no_alert_without_a_message_waiting_or_after_release(env):
    app, db, sent = env
    await subscribe(app)
    await start(app, "codex", "cx1", "codex")
    alone = (await write(app, "cx1", "solo")).json()["id"]
    assert await bridge_delivers(app, "cx1") == alone
    age_hold(db, alone, 3600)
    await sweep(app)
    assert db.get_request(alone).hold_alerted_at is None  # nothing behind it: not marked either
    behind = (await write(app, "cx1", "dopo")).json()["id"]
    async with human_client(app) as human:
        assert (await human.post(f"/api/requests/{alone}/queue/release", json={"reason": "finished"})).status_code == 200
    await sweep(app)
    assert [p for p in sent if p["tag"].startswith("queue-held")] == []
    assert db.get_request(behind).status.value in ("APPROVED", "DELIVERING")


@pytest.mark.asyncio
async def test_the_alert_follows_the_notification_preference(env):
    app, db, sent = env
    await subscribe(app)
    held, _ = await held_with_one_waiting(app, db)
    age_hold(db, held, 3600)
    db.unconfirmed_starts()
    age_hold(db, held, 3600)
    prefs = db.get_notification_preferences()
    assert prefs["queue_held"] is True  # on by default
    db.set_notification_preferences({**prefs, "queue_held": False})
    await sweep(app)
    assert [p for p in sent if p["tag"].startswith("queue-held")] == []
    db.set_notification_preferences({**prefs, "queue_held": True})
    await sweep(app)
    assert len([p for p in sent if p["tag"].startswith("queue-held")]) == 1


@pytest.mark.asyncio
async def test_the_preference_is_saved_through_the_api(env):
    app, _, _ = env
    async with human_client(app) as human:
        got = (await human.get("/api/settings/notifications")).json()
        assert got["queue_held"] is True
        put = await human.put("/api/settings/notifications", json={**got, "queue_held": False})
        assert put.json()["queue_held"] is False
        assert (await human.put("/api/settings/notifications",
                                json={**got, "queue_held": "maybe"})).status_code == 422
