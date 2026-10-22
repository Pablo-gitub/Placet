"""
Tests for scheduled messages storage and inbox delivery filtering (Part 1, RF-65).
"""
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.database import Database
from backend.app.models import Priority, QuestionType, RequestCreate, RequestModel, RequestStatus, SessionModel


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "placet.db"))
    database.init_db()
    return database


def _create_idle_session(db: Database, agent_id: str = "gemini", provider: str = "antigravity", session_id: str = "sess-1") -> SessionModel:
    now = datetime.now(timezone.utc).isoformat()
    with db._get_connection() as conn:
        conn.execute(
            """
            INSERT INTO sessions (
                id, agent_id, provider, entrypoint, cwd, title,
                tmux_session, state, turn_request_id, started_at, last_seen_at
            ) VALUES (?, ?, ?, 'cli', '/test', 'test session', 'agent-gemini', 'idle', NULL, ?, ?)
            """,
            (session_id, agent_id, provider, now, now),
        )
    return db.get_session(session_id)


def test_migration_adds_scheduled_at_to_requests(tmp_path):
    """An older database missing scheduled_at column gets migrated upon init_db."""
    db_path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(db_path)
    # Create bare minimal older requests table without scheduled_at
    conn.execute("""
        CREATE TABLE requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id TEXT NOT NULL,
            recipient_id TEXT NOT NULL,
            question_type TEXT NOT NULL,
            prompt TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()

    db = Database(db_path)
    db.init_db()

    with db._get_connection() as c:
        cols = [r["name"] for r in c.execute("PRAGMA table_info(requests);").fetchall()]
    assert "scheduled_at" in cols


def test_request_model_and_create_scheduled_at(db):
    """RequestCreate stores scheduled_at and RequestModel exposes it."""
    scheduled_time = datetime.now(timezone.utc) + timedelta(hours=2)
    req = db.create_request(
        RequestCreate(
            sender_id="human",
            recipient_id="gemini",
            question_type=QuestionType.MESSAGE,
            prompt="Hello future message",
            scheduled_at=scheduled_time,
        )
    )
    assert req.scheduled_at is not None
    # Truncate to seconds comparison due to ISO string normalization
    assert req.scheduled_at.year == scheduled_time.year
    assert req.scheduled_at.hour == scheduled_time.hour
    assert req.scheduled_at.minute == scheduled_time.minute

    fetched = db.get_request(req.id)
    assert fetched is not None
    assert fetched.scheduled_at is not None
    assert fetched.scheduled_at.isoformat() == req.scheduled_at.isoformat()


def test_take_inbox_message_skips_future_scheduled_at(db):
    """An approved message scheduled in the future is never handed out."""
    session = _create_idle_session(db, agent_id="gemini", session_id="sess-idle")
    future_time = datetime.now(timezone.utc) + timedelta(hours=1)

    req = db.create_request(
        RequestCreate(
            sender_id="human",
            recipient_id="gemini",
            question_type=QuestionType.MESSAGE,
            prompt="Wait for future",
            scheduled_at=future_time,
        )
    )
    # Approve the request so it is eligible for injection
    db.update_request_decision(req.id, RequestStatus.APPROVED, actor="human")

    # Future scheduled message must NOT be available in inbox
    assert db.inbox_has_message(session) is False
    assert db.take_inbox_message(session) is None
    mail_sessions = db.bridge_sessions_with_mail()
    assert all(s.id != session.id for s in mail_sessions)


def test_take_inbox_message_delivers_when_scheduled_at_is_past_or_current(db):
    """An approved message scheduled in the past or current time is handed out."""
    session = _create_idle_session(db, agent_id="gemini", session_id="sess-past")
    past_time = datetime.now(timezone.utc) - timedelta(minutes=5)

    req = db.create_request(
        RequestCreate(
            sender_id="human",
            recipient_id="gemini",
            question_type=QuestionType.MESSAGE,
            prompt="Execute now",
            scheduled_at=past_time,
        )
    )
    db.update_request_decision(req.id, RequestStatus.APPROVED, actor="human")

    assert db.inbox_has_message(session) is True
    mail_sessions = db.bridge_sessions_with_mail()
    assert any(s.id == session.id for s in mail_sessions)

    result = db.take_inbox_message(session)
    assert result is not None
    kind, delivered_req = result
    assert kind == "injection"
    assert delivered_req.id == req.id
    assert delivered_req.status == RequestStatus.DELIVERING


def test_take_inbox_message_delivers_when_scheduled_at_is_none(db):
    """A standard message with scheduled_at=None continues to be handed out normally."""
    session = _create_idle_session(db, agent_id="gemini", session_id="sess-none")

    req = db.create_request(
        RequestCreate(
            sender_id="human",
            recipient_id="gemini",
            question_type=QuestionType.MESSAGE,
            prompt="Immediate message",
            scheduled_at=None,
        )
    )
    db.update_request_decision(req.id, RequestStatus.APPROVED, actor="human")

    assert db.inbox_has_message(session) is True
    result = db.take_inbox_message(session)
    assert result is not None
    kind, delivered_req = result
    assert kind == "injection"
    assert delivered_req.id == req.id


# --- Part 2: API and Composer scheduling tests ---

from backend.app.config import AgentSpec, PlacetConfig
from backend.app.main import create_app
from tests.helpers import agent_client, human_client

CONFIG = PlacetConfig(agents={
    "claude": AgentSpec(tmux_session="agent-claude", token="tok-claude", allowed_targets=["human"]),
    "gemini": AgentSpec(tmux_session="agent-gemini", token="tok-gemini", allowed_targets=["human"]),
})


@pytest.fixture
def app_and_db(tmp_path):
    database = Database(str(tmp_path / "placet.db"))
    database.init_db()
    app = create_app(db=database, config=CONFIG, bridge_token="tok-bridge")
    return app, database


@pytest.mark.asyncio
async def test_schedule_api_human_only(app_and_db):
    app, db = app_and_db
    req = db.create_request(
        RequestCreate(sender_id="human", recipient_id="gemini", prompt="Test")
    )
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()

    # Agent cannot schedule requests (human only)
    async with agent_client(app, "tok-gemini") as client:
        resp = await client.post(f"/api/requests/{req.id}/schedule", json={"scheduled_at": future})
        assert resp.status_code == 403

    # Human can schedule
    async with human_client(app) as human:
        resp = await human.post(f"/api/requests/{req.id}/schedule", json={"scheduled_at": future})
        assert resp.status_code == 200
        data = resp.json()
        assert data["scheduled_at"] is not None


@pytest.mark.asyncio
async def test_schedule_api_only_while_not_yet_delivered(app_and_db):
    app, db = app_and_db
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

    # 1. PENDING request
    req_pending = db.create_request(RequestCreate(sender_id="human", recipient_id="gemini", prompt="Pending"))
    async with human_client(app) as human:
        resp = await human.post(f"/api/requests/{req_pending.id}/schedule", json={"scheduled_at": future})
        assert resp.status_code == 200

    # 2. APPROVED with delivery waiting
    req_approved = db.create_request(RequestCreate(sender_id="human", recipient_id="gemini", prompt="Approved"))
    db.update_request_decision(req_approved.id, RequestStatus.APPROVED, actor="human")
    async with human_client(app) as human:
        resp = await human.post(f"/api/requests/{req_approved.id}/schedule", json={"scheduled_at": future})
        assert resp.status_code == 200

    # 3. DELIVERING (delivery in progress / already delivered) -> 409
    session = _create_idle_session(db, agent_id="gemini", session_id="sess-deliv")
    req_delivering = db.create_request(RequestCreate(sender_id="human", recipient_id="gemini", prompt="Delivering"))
    db.update_request_decision(req_delivering.id, RequestStatus.APPROVED, actor="human")
    taken = db.take_inbox_message(session)
    assert taken is not None
    async with human_client(app) as human:
        resp = await human.post(f"/api/requests/{req_delivering.id}/schedule", json={"scheduled_at": future})
        assert resp.status_code == 409

    # 4. COMPLETED -> 409
    req_completed = db.create_request(RequestCreate(sender_id="human", recipient_id="gemini", prompt="Completed"))
    db.update_request_decision(req_completed.id, RequestStatus.REJECTED, actor="human")
    async with human_client(app) as human:
        resp = await human.post(f"/api/requests/{req_completed.id}/schedule", json={"scheduled_at": future})
        assert resp.status_code == 409


@pytest.mark.asyncio
async def test_schedule_api_rejects_past_and_too_far(app_and_db):
    app, db = app_and_db
    req = db.create_request(RequestCreate(sender_id="human", recipient_id="gemini", prompt="Timing"))

    async with human_client(app) as human:
        # > 1 min in the past -> 422
        past_2m = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        resp = await human.post(f"/api/requests/{req.id}/schedule", json={"scheduled_at": past_2m})
        assert resp.status_code == 422

        # <= 1 min in the past -> allowed
        past_20s = (datetime.now(timezone.utc) - timedelta(seconds=20)).isoformat()
        resp = await human.post(f"/api/requests/{req.id}/schedule", json={"scheduled_at": past_20s})
        assert resp.status_code == 200

        # > 30 days ahead -> 422
        far_future = (datetime.now(timezone.utc) + timedelta(days=31)).isoformat()
        resp = await human.post(f"/api/requests/{req.id}/schedule", json={"scheduled_at": far_future})
        assert resp.status_code == 422

        # 20 days ahead -> allowed
        ok_future = (datetime.now(timezone.utc) + timedelta(days=20)).isoformat()
        resp = await human.post(f"/api/requests/{req.id}/schedule", json={"scheduled_at": ok_future})
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_schedule_api_clear_schedule_and_events(app_and_db):
    app, db = app_and_db
    req = db.create_request(RequestCreate(sender_id="human", recipient_id="gemini", prompt="Clear me"))
    future = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()

    async with human_client(app) as human:
        # Schedule it
        resp = await human.post(f"/api/requests/{req.id}/schedule", json={"scheduled_at": future})
        assert resp.status_code == 200
        events = db.list_events(req.id)
        assert any(e.event == "scheduled" for e in events)

        # Clear it
        resp = await human.post(f"/api/requests/{req.id}/schedule", json={"scheduled_at": None})
        assert resp.status_code == 200
        assert resp.json()["scheduled_at"] is None
        events = db.list_events(req.id)
        assert any(e.event == "schedule_cleared" for e in events)


@pytest.mark.asyncio
async def test_write_to_session_with_scheduled_at(app_and_db):
    app, db = app_and_db
    session = _create_idle_session(db, agent_id="gemini", session_id="sess-write")
    future = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()

    async with human_client(app) as human:
        # 1. Invalid time in past -> 422
        past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        resp = await human.post(
            f"/api/sessions/{session.id}/messages",
            json={"text": "Hello past", "scheduled_at": past},
        )
        assert resp.status_code == 422

        # 2. Invalid time > 30 days -> 422
        too_far = (datetime.now(timezone.utc) + timedelta(days=32)).isoformat()
        resp = await human.post(
            f"/api/sessions/{session.id}/messages",
            json={"text": "Hello far", "scheduled_at": too_far},
        )
        assert resp.status_code == 422

        # 3. Valid future time -> 201
        resp = await human.post(
            f"/api/sessions/{session.id}/messages",
            json={"text": "Hello future", "scheduled_at": future},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["scheduled_at"] is not None
        req_id = data["id"]
        events = db.list_events(req_id)
        assert any(e.event == "scheduled" for e in events)

        # Verify it is not handed out yet because it is in the future
        assert db.inbox_has_message(session) is False


# --- Part 4: Deliver at reset (RF-66) tests ---

from backend.app.models import ProviderUsage
from tests.test_relations import two_nodes, post, event


_DEFAULT_RESET = object()


def set_provider_usage(db: Database, provider: str, five: float = None, seven: float = None, resets_at = _DEFAULT_RESET, observed = None, group = None):
    soon = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat() if resets_at is _DEFAULT_RESET else resets_at
    buckets = []
    if five is not None:
        buckets.append({"id": "5h", "group": group, "window": "5h", "used_percent": five, "resets_at": soon})
    if seven is not None:
        buckets.append({"id": "7d", "group": group, "window": "7d", "used_percent": seven, "resets_at": soon})
    row = ProviderUsage(provider=provider, observed_at=observed or datetime.now(timezone.utc), buckets=buckets)
    db.set_usage(provider, row.model_dump_json())


RELATION_CONFIG = PlacetConfig(agents={
    "claude": AgentSpec(tmux_session="agent-claude", token="tok-claude", allowed_targets=["human", "codex"]),
    "codex": AgentSpec(tmux_session="agent-codex", token="tok-codex", allowed_targets=["human", "claude"]),
})


@pytest.fixture
def rel_app_and_db(tmp_path):
    database = Database(str(tmp_path / "placet.db"))
    database.init_db()
    app = create_app(db=database, config=RELATION_CONFIG, bridge_token="tok-bridge")
    return app, database


@pytest.mark.asyncio
async def test_deliver_at_reset_settings_api(rel_app_and_db):
    app, db = rel_app_and_db
    # Default is false
    async with human_client(app) as human:
        resp = await human.get("/api/settings/deliver-at-reset")
        assert resp.status_code == 200
        assert resp.json() == {"enabled": False}

        # Toggle to true
        resp = await human.put("/api/settings/deliver-at-reset", json={"enabled": True})
        assert resp.status_code == 200
        assert resp.json() == {"enabled": True}

        # Verify persisted
        assert db.get_setting("deliver_at_reset") == "true"
        assert db.get_setting("deliver_at_reset_enabled") is None

        # Toggle back to false
        resp = await human.put("/api/settings/deliver-at-reset", json={"enabled": False})
        assert resp.status_code == 200
        assert resp.json() == {"enabled": False}
        assert db.get_setting("deliver_at_reset") == "false"
        assert db.get_setting("deliver_at_reset_enabled") is None

    # Agent cannot access
    async with agent_client(app, "tok-claude") as client:
        assert (await client.get("/api/settings/deliver-at-reset")).status_code == 403
        assert (await client.put("/api/settings/deliver-at-reset", json={"enabled": True})).status_code == 403


@pytest.mark.asyncio
async def test_deliver_at_reset_recipient_option_on_schedules_at_reset(rel_app_and_db):
    app, db = rel_app_and_db
    a, b = await two_nodes(app, db)
    review = db.create_relation_type("review", "")
    db.create_relation(a.id, b.id, review.id, "automatic")

    # Turn on usage guard and deliver at reset
    async with human_client(app) as human:
        await human.put("/api/settings/usage-guard", json={"enabled": True, "five_hour": 90, "seven_day": 95})
        await human.put("/api/settings/deliver-at-reset", json={"enabled": True})

    # Recipient (codex) is over 5h limit (92%), reset in 2 hours
    reset_target = (datetime.now(timezone.utc) + timedelta(hours=2))
    set_provider_usage(db, "codex", five=92, resets_at=reset_target.isoformat())

    # Send from claude to codex
    resp = await post(app, {"target_node": "builder"})
    out = resp.json()
    assert resp.status_code == 201
    assert out["status"] == "APPROVED"
    assert out["delivery_policy"] == "automatic"
    assert out["scheduled_at"] is not None
    # scheduled_at matches recipient's reset time
    sched = datetime.fromisoformat(out["scheduled_at"].replace("Z", "+00:00"))
    assert abs((sched - reset_target).total_seconds()) < 5

    # Recipient session does not have message in inbox yet because it is scheduled
    b_session = db.get_session("cx")
    assert db.inbox_has_message(b_session) is False

    # User can "send now" (clear schedule via schedule API)
    async with human_client(app) as human:
        clear_resp = await human.post(f"/api/requests/{out['id']}/schedule", json={"scheduled_at": None})
        assert clear_resp.status_code == 200
        assert clear_resp.json()["scheduled_at"] is None

    # Now it is available in inbox!
    assert db.inbox_has_message(b_session) is True


@pytest.mark.asyncio
async def test_deliver_at_reset_recipient_option_off_falls_back_to_manual(rel_app_and_db):
    app, db = rel_app_and_db
    a, b = await two_nodes(app, db)
    review = db.create_relation_type("review", "")
    db.create_relation(a.id, b.id, review.id, "automatic")

    # Turn on usage guard, but keep deliver at reset OFF
    async with human_client(app) as human:
        await human.put("/api/settings/usage-guard", json={"enabled": True, "five_hour": 90, "seven_day": 95})
        await human.put("/api/settings/deliver-at-reset", json={"enabled": False})

    # Recipient (codex) is over 5h limit (92%)
    reset_target = (datetime.now(timezone.utc) + timedelta(hours=2))
    set_provider_usage(db, "codex", five=92, resets_at=reset_target.isoformat())

    resp = await post(app, {"target_node": "builder"})
    out = resp.json()
    assert resp.status_code == 201
    assert out["status"] == "PENDING"
    assert out["delivery_policy"] == "manual"
    assert out["automation_limit"] == "usage"
    assert out["scheduled_at"] is None


@pytest.mark.asyncio
async def test_deliver_at_reset_unknown_or_stale_falls_back_to_manual(rel_app_and_db):
    app, db = rel_app_and_db
    a, b = await two_nodes(app, db)
    review = db.create_relation_type("review", "")
    db.create_relation(a.id, b.id, review.id, "automatic")

    # Turn on usage guard and deliver at reset
    async with human_client(app) as human:
        await human.put("/api/settings/usage-guard", json={"enabled": True, "five_hour": 90, "seven_day": 95})
        await human.put("/api/settings/deliver-at-reset", json={"enabled": True})

    # 1. Unknown reset time (resets_at is None)
    set_provider_usage(db, "codex", five=95, resets_at=None)
    resp = await post(app, {"target_node": "builder"})
    out = resp.json()
    assert out["status"] == "PENDING"
    assert out["delivery_policy"] == "manual"
    assert out["automation_limit"] == "usage"

    # 2. Multiple over-limit windows where one has unknown reset time
    soon = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    row = ProviderUsage(
        provider="codex",
        observed_at=datetime.now(timezone.utc),
        buckets=[
            {"id": "5h", "window": "5h", "used_percent": 95, "resets_at": None},
            {"id": "7d", "window": "7d", "used_percent": 98, "resets_at": soon},
        ],
    )
    db.set_usage("codex", row.model_dump_json())
    resp = await post(app, {"target_node": "builder"})
    out = resp.json()
    assert out["status"] == "PENDING"
    assert out["delivery_policy"] == "manual"
    assert out["automation_limit"] == "usage"


@pytest.mark.asyncio
async def test_deliver_at_reset_outcome_sender_over_limit(rel_app_and_db):
    app, db = rel_app_and_db
    a, b = await two_nodes(app, db)
    review = db.create_relation_type("review", "")
    # Automatic both directions
    db.create_relation(a.id, b.id, review.id, "automatic", reply_approval="automatic")

    # Turn on usage guard and deliver at reset
    async with human_client(app) as human:
        await human.put("/api/settings/usage-guard", json={"enabled": True, "five_hour": 90, "seven_day": 95})
        await human.put("/api/settings/deliver-at-reset", json={"enabled": True})

    # Claude (sender) is over limit (96%), reset in 3 hours; Codex (recipient) is fine
    sender_reset = (datetime.now(timezone.utc) + timedelta(hours=3))
    set_provider_usage(db, "claude-code", five=96, resets_at=sender_reset.isoformat())

    # Send prompt to Codex: prompt itself is NOT held because recipient is not over limit
    resp = await post(app, {"target_node": "builder"})
    req_data = resp.json()
    assert req_data["delivery_policy"] == "automatic"
    req_id = req_data["id"]

    # Prompt delivered to Codex
    codex_session = db.get_session("cx")
    taken = db.take_inbox_message(codex_session)
    assert taken is not None and taken[1].id == req_id

    # Codex replies
    async with agent_client(app, "tok-codex") as codex_client:
        reply_resp = await codex_client.post(f"/api/requests/{req_id}/reply", json={"content": "Done!"})
        assert reply_resp.status_code == 200

    # Because sender (claude-code) is over limit and deliver_at_reset is ON:
    # Outcome is scheduled at sender's reset time!
    completed_req = db.get_request(req_id)
    assert completed_req.status == RequestStatus.COMPLETED
    assert completed_req.scheduled_at is not None
    sched = completed_req.scheduled_at
    assert abs((sched - sender_reset).total_seconds()) < 5

    # Sender session (cl) is idle now
    await event(app, "claude", "cl", {"event": "stop", "provider": "claude-code"})
    cl_session = db.get_session("cl")

    # Outcome is NOT handed out to sender yet because it is scheduled
    assert db.inbox_has_message(cl_session) is False
    assert db.take_inbox_message(cl_session) is None

    # User can "send now" (clear schedule on outcome)
    async with human_client(app) as human:
        clear_resp = await human.post(f"/api/requests/{req_id}/schedule", json={"scheduled_at": None})
        assert clear_resp.status_code == 200

    # Now outcome is handed out to sender!
    assert db.inbox_has_message(cl_session) is True
    outcome_result = db.take_inbox_message(cl_session)
    assert outcome_result is not None
    kind, out_req = outcome_result
    assert kind == "outcome"
    assert out_req.id == req_id


@pytest.mark.asyncio
async def test_write_to_session_deliver_at_reset(rel_app_and_db):
    app, db = rel_app_and_db
    session = _create_idle_session(db, agent_id="codex", provider="codex", session_id="sess-cx")

    # Usage guard ON
    async with human_client(app) as human:
        await human.put("/api/settings/usage-guard", json={"enabled": True, "five_hour": 90, "seven_day": 95})

    # Provider over limit: codex 100% account limit
    soon = (datetime.now(timezone.utc) + timedelta(hours=1))
    set_provider_usage(db, "codex", five=100, resets_at=soon.isoformat())

    # 1. With deliver_at_reset OFF -> stays PENDING (manual approval)
    async with human_client(app) as human:
        await human.put("/api/settings/deliver-at-reset", json={"enabled": False})
        resp = await human.post(f"/api/sessions/{session.id}/messages", json={"text": "Message 1"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "PENDING"
        assert data["scheduled_at"] is None
        events = db.list_events(data["id"])
        assert any(e.event == "automation_limited" for e in events)

    # 2. With deliver_at_reset ON -> gets scheduled at reset time and APPROVED
    async with human_client(app) as human:
        await human.put("/api/settings/deliver-at-reset", json={"enabled": True})
        resp = await human.post(f"/api/sessions/{session.id}/messages", json={"text": "Message 2"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "APPROVED"
        assert data["scheduled_at"] is not None
        sched = datetime.fromisoformat(data["scheduled_at"].replace("Z", "+00:00"))
        assert abs((sched - soon).total_seconds()) < 5

    # 3. Mode "now" sends at once even if over limit
    async with human_client(app) as human:
        resp = await human.post(f"/api/sessions/{session.id}/messages", json={"text": "Message 3", "mode": "now"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "APPROVED"
        assert data["scheduled_at"] is None


@pytest.mark.asyncio
async def test_decide_request_deliver_at_reset(rel_app_and_db):
    """Approval of a manual relation request schedules at reset if recipient is over limit."""
    app, db = rel_app_and_db
    a, b = await two_nodes(app, db)
    review = db.create_relation_type("review", "")
    # Manual relation
    db.create_relation(a.id, b.id, review.id, "manual")

    # Usage guard ON, deliver at reset ON
    async with human_client(app) as human:
        await human.put("/api/settings/usage-guard", json={"enabled": True, "five_hour": 90, "seven_day": 95})
        await human.put("/api/settings/deliver-at-reset", json={"enabled": True})

    # Recipient (codex) is over limit (92%), reset in 2 hours
    soon = datetime.now(timezone.utc) + timedelta(hours=2)
    set_provider_usage(db, "codex", five=92, resets_at=soon.isoformat())

    # 1. Prompt on manual relation is created as PENDING
    resp = await post(app, {"target_node": "builder"})
    data = resp.json()
    assert data["status"] == "PENDING"
    assert data["scheduled_at"] is None
    req_id = data["id"]

    # 2. Human approves request: with deliver_at_reset ON, it gets scheduled at recipient reset!
    async with human_client(app) as human:
        dec_resp = await human.post(f"/api/requests/{req_id}/decision", json={"status": "APPROVED"})
        assert dec_resp.status_code == 200
        decided = dec_resp.json()
        assert decided["status"] == "APPROVED"
        assert decided["scheduled_at"] is not None
        sched = datetime.fromisoformat(decided["scheduled_at"].replace("Z", "+00:00"))
        assert abs((sched - soon).total_seconds()) < 5

    # 3. With deliver_at_reset OFF: approval leaves scheduled_at as None (send now, not put back in PENDING)
    async with human_client(app) as human:
        await human.put("/api/settings/deliver-at-reset", json={"enabled": False})
        resp2 = await post(app, {"target_node": "builder"})
        req2_id = resp2.json()["id"]
        dec_resp2 = await human.post(f"/api/requests/{req2_id}/decision", json={"status": "APPROVED"})
        assert dec_resp2.status_code == 200
        decided2 = dec_resp2.json()
        assert decided2["status"] == "APPROVED"
        assert decided2["scheduled_at"] is None


@pytest.mark.asyncio
async def test_agent_cannot_create_scheduled_request(rel_app_and_db):
    """Agents cannot create scheduled requests via API (422 schedule.not_allowed)."""
    app, db = rel_app_and_db
    soon = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    async with agent_client(app, "tok-claude") as agent:
        resp = await agent.post("/api/requests", json={
            "recipient_id": "codex",
            "question_type": "prompt_review",
            "prompt": "Test agent scheduled",
            "scheduled_at": soon,
        })
        assert resp.status_code == 422
        err = resp.json()
        assert err["code"] == "schedule.not_allowed"

    # Agent without scheduled_at succeeds
    async with agent_client(app, "tok-claude") as agent:
        ok_resp = await agent.post("/api/requests", json={
            "recipient_id": "codex",
            "question_type": "prompt_review",
            "prompt": "Test agent unscheduled",
        })
        assert ok_resp.status_code == 201



