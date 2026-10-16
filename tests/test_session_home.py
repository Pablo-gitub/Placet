"""The PWA home: sessions added by the user, ordered by activity (RF-40, RF-41)."""
import os
import tempfile

import pytest

from backend.app.config import AgentSpec, PlacetConfig
from backend.app.database import Database
from backend.app.main import create_app
from tests.helpers import agent_client, human_client

CONFIG = PlacetConfig(agents={
    "claude": AgentSpec(tmux_session="agent-claude", token="tok-claude", allowed_targets=["human"]),
})


@pytest.fixture
def setup():
    db = Database(os.path.join(tempfile.mkdtemp(), "placet.db"))
    db.init_db()
    return create_app(db=db, config=CONFIG, bridge_token="tok-bridge"), db


def pinned_at(db):
    with db._get_connection() as conn:
        return conn.execute("SELECT pinned_at FROM sessions").fetchone()[0]


@pytest.mark.asyncio
async def test_pin_and_unpin(setup):
    app, db = setup
    async with agent_client(app, "tok-claude") as claude:
        await claude.post("/api/sessions/s1/events", json={"event": "start"})
    async with human_client(app) as human:
        assert (await human.get("/api/sessions")).json()[0]["pinned"] is False
        resp = await human.put("/api/sessions/s1/pin", json={"pinned": True})
        assert resp.status_code == 200 and resp.json()["pinned"] is True
        first = pinned_at(db)
        # Pinning again keeps the original time.
        await human.put("/api/sessions/s1/pin", json={"pinned": True})
        assert pinned_at(db) == first
        resp = await human.put("/api/sessions/s1/pin", json={"pinned": False})
        assert resp.json()["pinned"] is False
        assert (await human.put("/api/sessions/nope/pin", json={"pinned": True})).status_code == 404
    async with agent_client(app, "tok-claude") as claude:
        assert (await claude.put("/api/sessions/s1/pin", json={"pinned": True})).status_code == 403
    async with agent_client(app, "tok-bridge") as bridge:
        assert (await bridge.put("/api/sessions/s1/pin", json={"pinned": True})).status_code == 403


@pytest.mark.asyncio
async def test_activity_moves_with_turns_not_with_the_waiter(setup):
    app, db = setup
    async with agent_client(app, "tok-claude") as claude:
        await claude.post("/api/sessions/s1/events", json={"event": "start"})
        started = db.get_session("s1").activity_at
        await claude.get("/api/sessions/s1/inbox")  # the waiter polls
        assert db.get_session("s1").activity_at == started
        await claude.post("/api/sessions/s1/events", json={"event": "prompt"})
        prompted = db.get_session("s1").activity_at
        assert prompted > started
        await claude.post("/api/sessions/s1/events", json={"event": "stop", "last_message": "fatto"})
        assert db.get_session("s1").activity_at > prompted


def test_existing_databases_get_the_new_columns(tmp_path):
    path = str(tmp_path / "old.db")
    import sqlite3
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE sessions (id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, provider TEXT,
        entrypoint TEXT, cwd TEXT, title TEXT, tmux_session TEXT, state TEXT NOT NULL DEFAULT 'idle',
        turn_request_id INTEGER, started_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
        waiter_seen_at TEXT, ended_at TEXT)""")
    conn.execute("INSERT INTO sessions (id, agent_id, started_at, last_seen_at) VALUES "
                 "('old', 'claude', '2026-10-01T10:00:00+00:00', '2026-10-01T10:00:00+00:00')")
    conn.commit()
    conn.close()
    db = Database(path)
    db.init_db()
    session = db.get_session("old")
    assert session.pinned is False and session.activity_at == session.started_at
    assert db.set_session_pinned("old", True) and db.get_session("old").pinned
