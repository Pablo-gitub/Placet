"""
The user writes to a session from the PWA (RF-34); the message reaches it
through its channel: the Claude waiter, or the bridge for Codex and
Antigravity (RF-24).
"""
import os
import tempfile
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from backend.app.config import AgentSpec, PlacetConfig
from backend.app.database import Database
from backend.app.envelopes import HOOK_PREFACE
from backend.app.main import create_app
from backend.app.models import QuestionType, RequestStatus
from bridge.bridge_daemon import BridgeDaemon
from bridge.channels import ChannelError
from bridge.stores import HttpStore
from tests.helpers import agent_client, human_client

CONFIG = PlacetConfig(agents={
    "claude": AgentSpec(tmux_session="agent-claude", token="tok-claude", allowed_targets=["human"]),
    "codex": AgentSpec(tmux_session="agent-codex", token="tok-codex", allowed_targets=["human"]),
    "gemini": AgentSpec(tmux_session="agent-gemini", token="tok-gemini", allowed_targets=["human"]),
})


@pytest.fixture
def setup():
    db = Database(os.path.join(tempfile.mkdtemp(), "placet.db"))
    db.init_db()
    return create_app(db=db, config=CONFIG, bridge_token="tok-bridge"), db


async def start(app, agent, session_id, provider):
    async with agent_client(app, f"tok-{agent}") as client:
        resp = await client.post(f"/api/sessions/{session_id}/events",
                                 json={"event": "start", "provider": provider})
        assert resp.status_code == 200


async def write(app, session_id, text="ciao dal telefono"):
    async with human_client(app) as human:
        return await human.post(f"/api/sessions/{session_id}/messages", json={"text": text})


@pytest.mark.asyncio
async def test_the_user_writes_without_approval(setup):
    app, db = setup
    await start(app, "claude", "c1", "claude-code")
    resp = await write(app, "c1")
    assert resp.status_code == 201
    req = resp.json()
    assert req["question_type"] == "message" and req["status"] == "APPROVED"
    assert req["sender_id"] == "human" and req["recipient_id"] == "claude"
    assert req["recipient_session_id"] == "c1"
    events = [e.event for e in db.list_events(req["id"])]
    assert events[:2] == ["created", "decided"]


@pytest.mark.asyncio
async def test_who_can_write_and_where(setup):
    app, db = setup
    await start(app, "claude", "c1", "claude-code")
    assert (await write(app, "nope")).status_code == 404
    assert (await write(app, "c1", "")).status_code == 422
    async with agent_client(app, "tok-claude") as claude:
        assert (await claude.post("/api/sessions/c1/messages", json={"text": "x"})).status_code == 403
        # Agents cannot forge a user message.
        resp = await claude.post("/api/requests", json={
            "recipient_id": "human", "question_type": "message", "prompt": "x"})
        assert resp.status_code == 422
        await claude.post("/api/sessions/c1/events", json={"event": "end"})
    assert (await write(app, "c1")).status_code == 409


@pytest.mark.asyncio
async def test_claude_session_gets_it_from_its_waiter_and_answers(setup):
    app, db = setup
    await start(app, "claude", "c1", "claude-code")
    req_id = (await write(app, "c1", "a che punto sei?")).json()["id"]
    async with agent_client(app, "tok-claude") as claude:
        mail = (await claude.get("/api/sessions/c1/inbox")).json()
        assert mail["kind"] == "injection" and mail["request_id"] == req_id
        assert mail["text"].startswith(f"[Placet Request #{req_id} dall'utente]: a che punto sei?")
        assert "placet_reply(" not in mail["text"] and HOOK_PREFACE in mail["text"]
        await claude.post("/api/sessions/c1/events", json={"event": "prompt", "request_id": req_id})
        assert db.get_request(req_id).status == RequestStatus.EXECUTING
        await claude.post("/api/sessions/c1/events", json={"event": "stop", "last_message": "Quasi finito."})
    done = db.get_request(req_id)
    assert done.status == RequestStatus.COMPLETED and done.response.content == "Quasi finito."


@pytest.mark.asyncio
async def test_bridge_wakes_codex_and_antigravity_sessions(setup):
    app, db = setup
    await start(app, "codex", "cx1", "codex")
    await start(app, "gemini", "ag1", "antigravity")
    await start(app, "claude", "c1", "claude-code")
    assert db.get_session("cx1").reachable and db.get_session("ag1").reachable
    assert not db.get_session("c1").reachable  # no waiter yet
    first = (await write(app, "cx1")).json()["id"]
    await write(app, "c1")
    async with agent_client(app, "tok-bridge") as bridge:
        work = (await bridge.get("/api/bridge/work")).json()
        assert work["sessions"] == [{"id": "cx1", "agent_id": "codex", "provider": "codex"}]
        mail = (await bridge.post("/api/bridge/sessions/cx1/take")).json()
        assert mail["request_id"] == first and HOOK_PREFACE not in mail["text"]
        assert db.get_request(first).status == RequestStatus.DELIVERING
        # Queuing failed: the message goes back to wait.
        await bridge.delete(f"/api/bridge/requests/{first}/claim")
        assert db.get_request(first).status == RequestStatus.APPROVED
        await bridge.post("/api/bridge/sessions/cx1/take")
        await bridge.post(f"/api/bridge/requests/{first}/injected")
        assert db.get_request(first).status == RequestStatus.EXECUTING
        assert (await bridge.post("/api/bridge/sessions/cx1/take")).status_code == 204
        # The Claude session is not the bridge's to wake.
        assert (await bridge.post("/api/bridge/sessions/c1/take")).status_code == 404
    async with agent_client(app, "tok-codex") as codex:
        assert (await codex.post("/api/bridge/sessions/cx1/take")).status_code == 403


@pytest.mark.asyncio
async def test_bridge_daemon_queues_into_the_app(setup):
    app, db = setup
    await start(app, "codex", "cx1", "codex")
    await start(app, "gemini", "ag1", "antigravity")
    ok = (await write(app, "cx1", "per codex")).json()["id"]
    failing = (await write(app, "ag1", "per antigravity")).json()["id"]

    codex, antigravity = MagicMock(), MagicMock()
    antigravity.send.side_effect = ChannelError("Antigravity is not running")
    daemon = BridgeDaemon(
        store=HttpStore(token="tok-bridge", client=TestClient(app)),
        tmux=MagicMock(), channels={"codex": codex, "antigravity": antigravity},
    )
    daemon.run_loop_step()
    session, text = codex.send.call_args.args
    assert session == "cx1" and text.startswith(f"[Placet Request #{ok} dall'utente]: per codex")
    assert db.get_request(ok).status == RequestStatus.EXECUTING
    # Not queued: back to APPROVED, to be retried.
    assert db.get_request(failing).status == RequestStatus.APPROVED
    assert antigravity.send.call_args.args[0] == "ag1"


@pytest.mark.asyncio
async def test_messages_to_closed_bridge_sessions_wait(setup):
    app, db = setup
    await start(app, "codex", "cx1", "codex")
    await write(app, "cx1")
    async with agent_client(app, "tok-codex") as codex:
        await codex.post("/api/sessions/cx1/events", json={"event": "prompt"})
    # Busy: nothing to take until the turn ends.
    assert db.bridge_sessions_with_mail() == []
    async with agent_client(app, "tok-codex") as codex:
        await codex.post("/api/sessions/cx1/events", json={"event": "stop"})
    assert [s.id for s in db.bridge_sessions_with_mail()] == ["cx1"]


def test_message_type_is_listed():
    assert QuestionType("message") == QuestionType.MESSAGE


@pytest.mark.asyncio
async def test_mentions_resolve_sessions_without_creating_agent_requests(setup):
    app, db = setup
    # This test allows the source to consult both agents, as the real registry does.
    app_config_agent = CONFIG.agents["claude"]
    previous = app_config_agent.allowed_targets[:]
    app_config_agent.allowed_targets = ["human", "codex", "gemini"]
    try:
        await start(app, "claude", "c1", "claude-code")
        await start(app, "codex", "cx1", "codex")
        await start(app, "gemini", "ag1", "antigravity")
        async with human_client(app) as human:
            response = await human.post("/api/sessions/c1/messages", json={
                "text": "Chiedi a @Codex la revisione e a @Gemini i test",
                "mentions": [{"token": "@Codex", "session_id": "cx1"},
                             {"token": "@Gemini", "session_id": "ag1"}],
            })
        assert response.status_code == 201
        request = db.get_request(response.json()["id"])
        from backend.app.envelopes import injection
        for channel in ("hook", "app", "tmux"):
            message = injection(request, channel=channel)
            assert '"target_session": "cx1"' in message
            assert '"target_session": "ag1"' in message
            assert "placet_send_to_agent" in message
        assert db.bridge_sessions_with_mail() == []
        assert request.recipient_session_id == "c1"
    finally:
        app_config_agent.allowed_targets = previous


@pytest.mark.asyncio
async def test_invalid_mentions_do_not_enqueue_a_message(setup):
    app, db = setup
    await start(app, "claude", "c1", "claude-code")
    await start(app, "codex", "cx1", "codex")
    async with human_client(app) as human:
        for mention, expected in [
            ({"token": "@missing", "session_id": "cx1"}, 422),
            ({"token": "@x", "session_id": "unknown"}, 409),
            ({"token": "@x", "session_id": "c1"}, 422),
            ({"token": "@x", "session_id": "cx1"}, 422),  # forbidden route
        ]:
            response = await human.post("/api/sessions/c1/messages", json={"text": "chiedi a @x", "mentions": [mention]})
            assert response.status_code == expected
    assert db.inbox_has_message(db.get_session("c1")) is False


@pytest.mark.asyncio
async def test_a_followed_session_notifies_the_end_of_its_turn(setup, monkeypatch):
    app, db = setup
    sent = []

    async def fake_turn_end(session_id, label, last_message, cfg):
        sent.append((session_id, label, last_message))
        return True
    monkeypatch.setattr("backend.app.main.send_ntfy_turn_end", fake_turn_end)
    await start(app, "codex", "cx1", "codex")
    async with agent_client(app, "tok-codex") as codex:
        await codex.post("/api/sessions/cx1/events", json={"event": "prompt"})
        await codex.post("/api/sessions/cx1/events", json={"event": "stop", "last_message": "fatto"})
    assert sent == []  # not followed
    db.set_session_pinned("cx1", True)
    async with agent_client(app, "tok-codex") as codex:
        await codex.post("/api/sessions/cx1/events", json={"event": "prompt", "title": "Review PR"})
        await codex.post("/api/sessions/cx1/events", json={"event": "stop", "last_message": "fatto"})
        # A second turn right after: no second notification within the minute.
        await codex.post("/api/sessions/cx1/events", json={"event": "prompt"})
        await codex.post("/api/sessions/cx1/events", json={"event": "stop", "last_message": "ancora"})
        # A stop without a running turn (a repeated event) never notifies.
        await codex.post("/api/sessions/cx1/events", json={"event": "stop"})
    import asyncio
    await asyncio.sleep(0)
    assert sent == [("cx1", "Codex · Review PR", "fatto")]
