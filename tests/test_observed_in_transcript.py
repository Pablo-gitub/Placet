"""A message the recipient read but whose turn no hook reported (Codex took it while busy) is proven started by the transcript."""
import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from backend.app.database import Database
from bridge.bridge_daemon import BridgeDaemon
from bridge.stores import LocalStore
from tests.helpers import agent_client, human_client
from tests.test_request_aspects import bridge_delivers
from tests.test_session_messages import CONFIG, setup, start, write  # noqa: F401


def age(db, request_id, seconds):
    then = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
    with db._get_connection() as conn:
        conn.execute("UPDATE requests SET delivery_at = ? WHERE id = ?", (then, request_id))


async def work(app):
    async with agent_client(app, "tok-bridge") as bridge:
        return (await bridge.get("/api/bridge/work")).json()


async def delivered_but_unseen(app, db):
    await start(app, "codex", "cx1", "codex")
    rid = (await write(app, "cx1", "mentre lavori")).json()["id"]
    assert await bridge_delivers(app, "cx1") == rid
    return rid


@pytest.mark.asyncio
async def test_a_delivered_message_with_no_observed_turn_is_listed_for_the_bridge_after_a_while(setup):  # noqa: F811
    app, db = setup
    rid = await delivered_but_unseen(app, db)
    assert (await work(app))["unobserved"] == []                                  # too fresh: the hook may still report it
    age(db, rid, 60)
    assert (await work(app))["unobserved"] == [{"request_id": rid, "session_id": "cx1", "agent_id": "codex", "provider": "codex"}]


@pytest.mark.asyncio
async def test_the_transcript_proves_the_start_and_only_once(setup):  # noqa: F811
    app, db = setup
    rid = await delivered_but_unseen(app, db)
    async with agent_client(app, "tok-bridge") as bridge:
        resp = await bridge.post(f"/api/bridge/requests/{rid}/observed")
        assert resp.status_code == 200 and resp.json()["execution"] == "started"
        assert (await bridge.post(f"/api/bridge/requests/{rid}/observed")).status_code == 409            # already settled
    req = db.get_request(rid)
    assert req.execution == "started" and req.execution_source == "transcript" and req.status.value == "EXECUTING"
    assert any(e.event == "execution_started" and e.details.get("source") == "transcript" for e in db.list_events(rid))
    age(db, rid, 60)
    assert (await work(app))["unobserved"] == []


@pytest.mark.asyncio
async def test_only_the_bridge_proves_and_only_an_executing_unobserved_request(setup):  # noqa: F811
    app, db = setup
    await start(app, "codex", "cx1", "codex")
    waiting = (await write(app, "cx1", "in attesa")).json()["id"]
    async with agent_client(app, "tok-bridge") as bridge:
        assert (await bridge.post(f"/api/bridge/requests/{waiting}/observed")).status_code == 409        # not delivered yet
        assert (await bridge.post("/api/bridge/requests/9999/observed")).status_code == 404
    async with agent_client(app, "tok-codex") as agent:
        assert (await agent.post(f"/api/bridge/requests/{waiting}/observed")).status_code == 403
    async with human_client(app) as human:
        assert (await human.post(f"/api/bridge/requests/{waiting}/observed")).status_code == 403


# --- the bridge looks in the conversation ----------------------------------------------------------


def test_the_bridge_marks_a_message_it_finds_in_the_conversation_and_throttles_the_look():
    store = MagicMock()
    daemon = BridgeDaemon(store=store, tmux=MagicMock(), channels={}, claude_waker=MagicMock())
    item = {"request_id": 7, "session_id": "cx1", "agent_id": "codex", "provider": "codex"}
    page = {"messages": [{"role": "user", "text": "x", "request_id": 7}]}
    with patch("bridge.bridge_daemon.read_transcript", return_value=page) as read:
        assert daemon.reconcile_unobserved([item]) == 1
        assert daemon.reconcile_unobserved([item]) == 0                                              # not again at once
    store.mark_observed.assert_called_once_with(7)
    assert read.call_count == 1


def test_a_message_not_in_the_conversation_is_left_alone():
    store = MagicMock()
    daemon = BridgeDaemon(store=store, tmux=MagicMock(), channels={}, claude_waker=MagicMock())
    item = {"request_id": 7, "session_id": "cx1", "agent_id": "codex", "provider": "codex"}
    with patch("bridge.bridge_daemon.read_transcript", return_value={"messages": [{"role": "user", "text": "x", "request_id": 8}]}):
        assert daemon.reconcile_unobserved([item]) == 0
    store.mark_observed.assert_not_called()


def test_an_unreadable_conversation_never_breaks_the_bridge():
    from bridge.transcripts import TranscriptError
    store = MagicMock()
    daemon = BridgeDaemon(store=store, tmux=MagicMock(), channels={}, claude_waker=MagicMock())
    with patch("bridge.bridge_daemon.read_transcript", side_effect=TranscriptError("not_found", "gone")):
        assert daemon.reconcile_unobserved([{"request_id": 7, "session_id": "cx1", "agent_id": "codex", "provider": "codex"}]) == 0


def test_the_local_store_lists_and_marks_too():
    db = Database(os.path.join(tempfile.mkdtemp(), "placet.db"))
    db.init_db()
    work = LocalStore(db, CONFIG).work()
    assert work.unobserved == []
