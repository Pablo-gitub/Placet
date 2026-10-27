"""A delivery the channel can never complete (Antigravity exposes no configuration for the conversation) is a visible,
per-session block with its own backoff, not a silent retry every few seconds."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.database import Database
from backend.app.main import create_app
from backend.app.webpush import WebPush
from tests.helpers import agent_client, human_client
from tests.test_session_messages import CONFIG, start, write

SID = "ag1"


@pytest.fixture
def env(tmp_path):
    db = Database(str(tmp_path / "placet.db"))
    db.init_db()
    sent = []

    def fake_send(sub, payload, urgency):
        sent.append(json.loads(payload))
        return 201

    push = WebPush(db, subject="https://host.example", send=fake_send)
    return create_app(db=db, config=CONFIG, bridge_token="tok-bridge", web_push=push), db, sent


async def subscribe(app):
    sub = {"endpoint": "https://web.push.apple.com/QGx1", "keys": {"p256dh": "BPk", "auth": "au"}}
    async with human_client(app) as human:
        await human.post("/api/push/subscription", json=sub)


async def block(app, code="missing_user_config", phase="pre_dispatch", deterministic=True, attempts=1, session=SID):
    async with agent_client(app, "tok-bridge") as bridge:
        resp = await bridge.post(f"/api/bridge/sessions/{session}/delivery-block", json={
            "code": code, "phase": phase, "deterministic": deterministic, "attempts": attempts})
        assert resp.status_code == 200, resp.text
        await asyncio.sleep(0.05)                                                  # the push leaves from a worker thread
        return resp.json()


async def work(app):
    async with agent_client(app, "tok-bridge") as bridge:
        return (await bridge.get("/api/bridge/work")).json()


def make_due(db, seconds_ago=1):
    then = (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()
    with db._get_connection() as conn:
        conn.execute("UPDATE delivery_blocks SET next_retry_at = ?", (then,))


def delay(db):
    row = db.get_delivery_block(SID)
    return (datetime.fromisoformat(row["next_retry_at"]) - datetime.fromisoformat(row["last_checked_at"])).total_seconds()


async def waiting_session(app, count=1):
    await start(app, "gemini", SID, "antigravity")
    return [(await write(app, SID, f"messaggio {i}")).json()["id"] for i in range(count)]


@pytest.mark.asyncio
async def test_a_reported_block_is_kept_with_its_reason_and_counted_attempts(env):
    app, db, sent = env
    await waiting_session(app)
    await block(app, attempts=2)                                                   # the first try and its confirmation
    row = db.get_delivery_block(SID)
    assert (row["code"], row["phase"], row["failed_attempts"]) == ("missing_user_config", "pre_dispatch", 2)
    assert row["since"] and row["last_checked_at"] and row["next_retry_at"] > row["last_checked_at"]
    await block(app)
    assert db.get_delivery_block(SID)["failed_attempts"] == 3                      # counted apart from delivery_attempts


@pytest.mark.asyncio
async def test_the_report_carries_only_a_code_and_a_phase_never_a_configuration(env):
    app, db, sent = env
    async with agent_client(app, "tok-bridge") as bridge:
        for body in ({"code": "missing_user_config", "phase": "pre_dispatch", "config": {"model": "SECRET"}},
                     {"code": "has spaces and CONFIG", "phase": "pre_dispatch"},
                     {"code": "x", "phase": "somewhere"}):
            assert (await bridge.post(f"/api/bridge/sessions/{SID}/delivery-block", json=body)).status_code == 422
    assert db.get_delivery_block(SID) is None


@pytest.mark.asyncio
async def test_a_blocked_session_leaves_the_bridge_work_until_its_retry_is_due(env):
    app, db, sent = env
    await waiting_session(app)
    assert [s["id"] for s in (await work(app))["sessions"]] == [SID]
    await block(app)
    data = await work(app)
    assert SID not in [s["id"] for s in data["sessions"]] and SID not in [s["id"] for s in data["wake_sessions"]]
    async with agent_client(app, "tok-bridge") as bridge:
        assert (await bridge.post(f"/api/bridge/sessions/{SID}/take")).status_code == 204     # nothing is handed out either
    make_due(db)
    listed = [s for s in (await work(app))["sessions"] if s["id"] == SID]
    assert listed and listed[0]["probing"] is True                                 # one probe, the bridge knows it is one
    async with agent_client(app, "tok-bridge") as bridge:
        assert (await bridge.post(f"/api/bridge/sessions/{SID}/take")).status_code == 200


@pytest.mark.asyncio
async def test_deterministic_checks_back_off_five_fifteen_then_sixty_minutes(env):
    app, db, sent = env
    await waiting_session(app)
    seen = []
    for _ in range(5):
        await block(app)
        seen.append(round(delay(db) / 60))
        make_due(db)
    assert seen == [5, 15, 60, 60, 60]


@pytest.mark.asyncio
async def test_transient_errors_back_off_exponentially_with_jitter_and_a_cap(env, monkeypatch):
    app, db, sent = env
    await waiting_session(app)
    monkeypatch.setattr("backend.app.database.random.uniform", lambda a, b: 1.0)           # no jitter: the pure curve
    delays = []
    for _ in range(9):
        await block(app, code="conversation_unavailable", deterministic=False)
        delays.append(round(delay(db)))
        make_due(db)
    assert delays == [6, 12, 24, 48, 96, 192, 300, 300, 300]
    monkeypatch.undo()
    await block(app, code="conversation_unavailable", deterministic=False)
    assert 0.8 * 300 - 1 <= delay(db) <= 1.2 * 300 + 1                                         # jitter stays within ±20 %


@pytest.mark.asyncio
async def test_three_requests_to_the_same_session_make_one_notification(env):
    app, db, sent = env
    await subscribe(app)
    await waiting_session(app, count=3)
    for _ in range(3):
        await block(app)
        make_due(db)
    alerts = [p for p in sent if p["tag"].startswith("delivery-blocked")]
    assert len(alerts) == 1 and alerts[0]["url"] == f"/#/sessione/{SID}"
    assert "configurazione" in alerts[0]["body"] and "SECRET" not in json.dumps(alerts[0])
    assert db.get_delivery_block(SID)["notified_at"] is not None


@pytest.mark.asyncio
async def test_a_new_message_to_the_blocked_session_does_not_clear_the_block(env):
    app, db, sent = env
    await waiting_session(app)
    await block(app)
    before = db.get_delivery_block(SID)
    await write(app, SID, "un altro")
    assert db.get_delivery_block(SID)["next_retry_at"] == before["next_retry_at"]
    assert SID not in [s["id"] for s in (await work(app))["sessions"]]


@pytest.mark.asyncio
async def test_the_users_retry_makes_it_due_at_once_and_keeps_the_episode(env):
    app, db, sent = env
    await subscribe(app)
    await waiting_session(app)
    await block(app)
    await block(app)                                                              # level 2
    async with human_client(app) as human:
        assert (await human.post(f"/api/sessions/{SID}/delivery-block/retry")).status_code == 200
    assert [s["id"] for s in (await work(app))["sessions"]] == [SID]
    await block(app)                                                              # still failing: back to the first backoff
    assert round(delay(db) / 60) == 5
    assert len([p for p in sent if p["tag"].startswith("delivery-blocked")]) == 1       # the same episode: no second alert


@pytest.mark.asyncio
async def test_only_the_human_retries_and_without_a_block_there_is_nothing_to_retry(env):
    app, db, sent = env
    await waiting_session(app)
    async with human_client(app) as human:
        assert (await human.post(f"/api/sessions/{SID}/delivery-block/retry")).status_code == 404
    await block(app)
    async with agent_client(app, "tok-gemini") as agent:
        assert (await agent.post(f"/api/sessions/{SID}/delivery-block/retry")).status_code == 403
    async with agent_client(app, "tok-gemini") as agent:
        assert (await agent.post(f"/api/bridge/sessions/{SID}/delivery-block", json={"code": "x", "phase": "pre_dispatch"})).status_code == 403


@pytest.mark.asyncio
async def test_a_successful_delivery_ends_the_episode(env):
    app, db, sent = env
    await subscribe(app)
    await waiting_session(app)
    await block(app)
    async with agent_client(app, "tok-bridge") as bridge:
        assert (await bridge.delete(f"/api/bridge/sessions/{SID}/delivery-block")).status_code == 200
    assert db.get_delivery_block(SID) is None
    await block(app)                                                              # a new episode
    assert len([p for p in sent if p["tag"].startswith("delivery-blocked")]) == 2


@pytest.mark.asyncio
async def test_the_block_survives_a_restart(env, tmp_path):
    app, db, sent = env
    await waiting_session(app)
    await block(app)
    again = Database(str(tmp_path / "placet.db"))
    again.init_db()
    restarted = create_app(db=again, config=CONFIG, bridge_token="tok-bridge")
    assert SID not in [s["id"] for s in (await work(restarted))["sessions"]]
    assert again.get_delivery_block(SID)["code"] == "missing_user_config"


@pytest.mark.asyncio
async def test_the_held_requests_are_listed_with_the_reason_and_what_the_user_can_do(env):
    app, db, sent = env
    ids = await waiting_session(app, count=2)
    await block(app, attempts=2)
    async with human_client(app) as human:
        items = {i["request"]["id"]: i for i in (await human.get("/api/requests/undelivered")).json()}
    for rid in ids:
        item = items[rid]
        assert item["cause"] == "delivery_blocked"
        assert item["block"]["code"] == "missing_user_config" and item["block"]["failed_attempts"] == 2
        assert item["block"]["last_checked_at"] and "config" not in json.dumps(item["block"]).replace("missing_user_config", "")
        assert item["can_cancel"] is True and item["can_forward"] is True
        assert item["block"]["session_id"] == SID
