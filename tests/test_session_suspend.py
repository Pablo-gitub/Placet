"""
Codex's SessionEnd only means the app unloaded the conversation: the session
is suspended, its messages wait in Placet until it starts again.
"""
import sqlite3

import pytest

from backend.app.database import Database
from backend.app.models import RequestStatus
from tests.helpers import agent_client
from tests.test_session_messages import setup, start, write  # noqa: F401


async def event(app, agent, session_id, **body):
    async with agent_client(app, f"tok-{agent}") as client:
        resp = await client.post(f"/api/sessions/{session_id}/events", json=body)
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_codex_end_suspends_and_the_message_waits(setup):
    app, db = setup
    await start(app, "codex", "cx1", "codex")
    await event(app, "codex", "cx1", event="end")
    session = db.get_session("cx1")
    assert session.ended_at is None and session.suspended_at is not None
    assert not session.reachable
    assert [s.id for s in db.list_sessions()] == ["cx1"]

    resp = await write(app, "cx1", "riprendiamo")
    assert resp.status_code == 201
    req_id = resp.json()["id"]
    # Not handed to the bridge: codex queue would hold it unseen in the app.
    assert db.bridge_sessions_with_mail() == []
    async with agent_client(app, "tok-bridge") as bridge:
        assert (await bridge.get("/api/bridge/work")).json()["sessions"] == []

    # The user reopens the conversation in the app: it is delivered.
    await event(app, "codex", "cx1", event="start", provider="codex")
    assert db.get_session("cx1").reachable
    assert [s.id for s in db.bridge_sessions_with_mail()] == ["cx1"]
    assert db.get_request(req_id).status == RequestStatus.APPROVED


@pytest.mark.asyncio
async def test_a_late_event_does_not_reopen(setup):
    app, db = setup
    await start(app, "codex", "cx1", "codex")
    await event(app, "codex", "cx1", event="end")
    await event(app, "codex", "cx1", event="stop")
    assert db.get_session("cx1").suspended_at is not None
    await start(app, "claude", "c1", "claude-code")
    await event(app, "claude", "c1", event="end")
    await event(app, "claude", "c1", event="stop")
    assert db.get_session("c1").ended_at is not None
    # A turn is proof the runtime is alive again.
    await event(app, "codex", "cx1", event="prompt")
    session = db.get_session("cx1")
    assert session.suspended_at is None and session.state == "busy"


def test_codex_sessions_ended_before_become_suspended(tmp_path):
    path = str(tmp_path / "placet.db")
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE sessions (id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, provider TEXT,
            entrypoint TEXT, cwd TEXT, title TEXT, tmux_session TEXT,
            state TEXT NOT NULL DEFAULT 'idle', turn_request_id INTEGER, started_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL, waiter_seen_at TEXT, ended_at TEXT, activity_at TEXT,
            pinned_at TEXT, excluded_at TEXT);
        INSERT INTO sessions (id, agent_id, provider, started_at, last_seen_at, ended_at) VALUES
            ('cx1', 'codex', 'codex', '2026-10-16T20:00:00+00:00', '2026-10-17T02:24:00+00:00',
             '2026-10-17T02:24:42+00:00'),
            ('c1', 'claude', 'claude-code', '2026-10-16T20:00:00+00:00', '2026-10-17T02:24:00+00:00',
             '2026-10-17T02:24:42+00:00');
    """)
    conn.close()
    db = Database(path)
    db.init_db()
    codex, claude = db.get_session("cx1"), db.get_session("c1")
    assert codex.ended_at is None and codex.suspended_at is not None
    assert claude.ended_at is not None and claude.suspended_at is None

@pytest.mark.asyncio
async def test_claude_desktop_end_suspends_and_can_receive_mail(setup):
    app, db = setup
    sid = 'e24d69ad-faf5-43c9-94c4-a8e8a6e886a0'
    await event(app, 'claude', sid, event='start', provider='claude-code',
                entrypoint='claude-desktop', cwd='/lab')
    await event(app, 'claude', sid, event='end')
    session = db.get_session(sid)
    assert session.ended_at is None and session.suspended_at is not None
    assert not session.reachable
    response = await write(app, sid, 'wake existing conversation')
    assert response.status_code == 201
    assert [s.id for s in db.claude_sessions_to_wake()] == [sid]
    await event(app, 'claude', sid, event='stop')
    assert db.get_session(sid).suspended_at is not None
    await event(app, 'claude', sid, event='start')
    db.touch_waiter(sid)
    assert db.get_session(sid).reachable
    assert db.claude_sessions_to_wake() == []


def test_old_closed_claude_desktop_recovers_once_without_reopening_cli(tmp_path):
    path = str(tmp_path / 'placet.db')
    db = Database(path)
    db.init_db()
    db.upsert_session('desktop', 'claude', provider='claude-code', entrypoint='claude-desktop')
    db.upsert_session('cli', 'claude', provider='claude-code', entrypoint='cli')
    for sid in ('desktop', 'cli'):
        db.end_session(sid)
    with sqlite3.connect(path) as conn:
        conn.execute('DELETE FROM schema_migrations WHERE name = ?', ('claude_desktop_end_is_suspension_v1',))
    db.init_db()
    desktop, cli = db.get_session('desktop'), db.get_session('cli')
    assert desktop.ended_at is None and desktop.suspended_at is not None
    assert cli.ended_at is not None and cli.suspended_at is None
    # A later genuine closure is not repeatedly undone at every startup.
    db.end_session('desktop')
    db.init_db()
    assert db.get_session('desktop').ended_at is not None
