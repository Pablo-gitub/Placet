"""Leftovers of the scheduled-messages review: migration of the expiry window, and the 'now' conflict."""
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.models import QuestionType, RequestCreate, RequestStatus
from backend.app.database import Database
from tests.helpers import human_client
from tests.test_scheduled_messages import _create_idle_session, rel_app_and_db  # noqa: F401


def make_legacy(db):
    """The database as it was before expires_in_seconds existed (its requests keep their expires_at)."""
    with db._get_connection() as conn:
        conn.execute("ALTER TABLE requests DROP COLUMN expires_in_seconds")


def fresh(path):
    db = Database(path)
    db.init_db()
    return db


def test_the_migration_keeps_each_old_requests_own_expiry_window(tmp_path):
    path = str(tmp_path / "placet.db")
    db = fresh(path)
    short = db.create_request(RequestCreate(sender_id="human", recipient_id="gemini", prompt="a", expires_in_seconds=3600))
    long = db.create_request(RequestCreate(sender_id="human", recipient_id="gemini", prompt="b", expires_in_seconds=3 * 86400))
    zero = db.create_request(RequestCreate(sender_id="human", recipient_id="gemini", prompt="c"))
    with db._get_connection() as conn:
        conn.execute("UPDATE requests SET expires_at = NULL WHERE id = ?", (long.id,))
        conn.execute("UPDATE requests SET expires_at = created_at WHERE id = ?", (zero.id,))
    make_legacy(db)
    reopened = Database(path)
    reopened.init_db()
    windows = {r.id: r.expires_in_seconds for r in (reopened.get_request(i) for i in (short.id, long.id, zero.id))}
    assert windows[short.id] == 3600                 # its own window, not 24 h
    assert windows[long.id] == 86400                 # no expiry recorded: the default
    assert windows[zero.id] == 86400             # a non-positive window is not trusted


def test_a_rescheduled_old_request_keeps_its_short_window(tmp_path):
    path = str(tmp_path / "placet.db")
    db = fresh(path)
    req = db.create_request(RequestCreate(sender_id="human", recipient_id="gemini", prompt="a", expires_in_seconds=3600))
    make_legacy(db)
    reopened = Database(path)
    reopened.init_db()
    later = datetime.now(timezone.utc) + timedelta(hours=5)
    updated = reopened.schedule_request(req.id, later, actor="human")
    assert abs((updated.expires_at - later).total_seconds() - 3600) < 5


def test_the_migration_runs_once(tmp_path):
    path = str(tmp_path / "placet.db")
    db = fresh(path)
    req = db.create_request(RequestCreate(sender_id="human", recipient_id="gemini", prompt="a", expires_in_seconds=3600))
    make_legacy(db)
    first = Database(path)
    first.init_db()
    with first._get_connection() as conn:
        conn.execute("UPDATE requests SET expires_in_seconds = 999 WHERE id = ?", (req.id,))
    again = Database(path)
    again.init_db()
    assert again.get_request(req.id).expires_in_seconds == 999


@pytest.mark.asyncio
async def test_approving_now_conflicts_only_with_a_schedule_still_ahead(rel_app_and_db):  # noqa: F811
    app, db = rel_app_and_db
    session = _create_idle_session(db, agent_id="claude", provider="claude-code", session_id="sess-now")

    def scheduled(delta):
        req = db.create_request(RequestCreate(
            sender_id="human", recipient_id="claude", prompt="p", recipient_session_id=session.id,
            scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1)))
        with db._get_connection() as conn:
            conn.execute("UPDATE requests SET scheduled_at = ? WHERE id = ?",
                         ((datetime.now(timezone.utc) + delta).isoformat(), req.id))
        return req

    async def approve_now(req):
        async with human_client(app) as human:
            return await human.post(f"/api/requests/{req.id}/decision",
                                    json={"status": "APPROVED", "delivery_mode": "now", "recipient_session_id": session.id})

    ahead = await approve_now(scheduled(timedelta(minutes=30)))
    assert ahead.status_code == 422 and ahead.json()["code"] == "schedule.steer_conflict"
    due = await approve_now(scheduled(timedelta(minutes=-5)))                 # the schedule has already passed
    assert not (due.status_code == 422 and due.json().get("code") == "schedule.steer_conflict")
    assert due.status_code == 200, due.text


def test_a_scheduled_old_request_counts_its_window_from_the_schedule(tmp_path):
    """expires_at of a scheduled request is scheduled_at + window, not created_at + window."""
    path = str(tmp_path / "placet.db")
    db = fresh(path)
    later = datetime.now(timezone.utc) + timedelta(hours=5)
    req = db.create_request(RequestCreate(sender_id="human", recipient_id="gemini", prompt="a",
                                          expires_in_seconds=3600, scheduled_at=later))
    make_legacy(db)
    assert fresh(path).get_request(req.id).expires_in_seconds == 3600
