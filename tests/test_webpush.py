"""Web Push to the installed PWA: subscriptions, keys and delivery."""
import asyncio
import json

import pytest

from backend.app.database import Database
from backend.app.main import create_app
from backend.app.webpush import WebPush, allowed_endpoint
from tests.helpers import agent_client, human_client
from tests.test_session_messages import CONFIG

APPLE = "https://web.push.apple.com/QGx1"


@pytest.fixture
def env(tmp_path):
    db = Database(str(tmp_path / "placet.db"))
    db.init_db()
    sent = []

    def fake_send(sub, payload, urgency):
        sent.append((sub["endpoint"], json.loads(payload), urgency))
        return 410 if "gone" in sub["endpoint"] else 201

    push = WebPush(db, subject="https://host.example", send=fake_send)
    return create_app(db=db, config=CONFIG, web_push=push), db, sent


def test_only_browser_push_services_are_allowed():
    assert allowed_endpoint(APPLE)
    assert allowed_endpoint("https://fcm.googleapis.com/fcm/send/x")
    for bad in ("http://web.push.apple.com/x", "https://evil.example/x", "https://push.apple.com.evil.example/x",
                "https://127.0.0.1/x", ""):
        assert not allowed_endpoint(bad)


def test_vapid_key_is_generated_once_and_kept(tmp_path):
    db = Database(str(tmp_path / "k.db"))
    db.init_db()
    key = WebPush(db).public_key()
    assert len(key) == 87  # uncompressed P-256 point, base64url
    assert WebPush(db).public_key() == key


@pytest.mark.asyncio
async def test_subscribe_and_receive_a_question(env):
    app, db, sent = env
    sub = {"endpoint": APPLE, "keys": {"p256dh": "BPk", "auth": "au"}}
    async with human_client(app) as human:
        assert (await human.post("/api/push/subscription", json=sub)).status_code == 204
        bad = {**sub, "endpoint": "https://evil.example/x"}
        resp = await human.post("/api/push/subscription", json=bad)
        assert resp.status_code == 422 and resp.json()["code"] == "push.invalid"
    async with agent_client(app, "tok-claude") as client:
        resp = await client.post("/api/requests", json={"recipient_id": "human", "prompt": "secret plan"})
        req_id = resp.json()["id"]
    for _ in range(50):  # the push is sent in the background
        if sent:
            break
        await asyncio.sleep(0.01)
    endpoint, payload, urgency = sent[-1]
    assert endpoint == APPLE and payload["url"] == f"/#request-{req_id}" and urgency == "high"
    # Only a short announcement leaves the machine, never the prompt.
    assert "secret plan" not in json.dumps(payload)
    async with human_client(app) as human:
        assert (await human.post("/api/push/test")).json() == {"sent": 1}
        await human.request("DELETE", "/api/push/subscription", json={"endpoint": APPLE})
    assert db.list_push_subscriptions() == []


@pytest.mark.asyncio
async def test_gone_subscriptions_are_forgotten(env):
    app, db, sent = env
    db.add_push_subscription("https://web.push.apple.com/gone", "k", "a", None)
    async with human_client(app) as human:
        assert (await human.post("/api/push/test")).json() == {"sent": 0}
    assert db.list_push_subscriptions() == []


@pytest.mark.asyncio
async def test_agents_cannot_subscribe(env):
    app, db, sent = env
    async with agent_client(app, "tok-claude") as client:
        resp = await client.post("/api/push/subscription", json={"endpoint": APPLE, "keys": {"p256dh": "a", "auth": "b"}})
        assert resp.status_code in (401, 403, 404)
