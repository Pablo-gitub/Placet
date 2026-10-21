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
