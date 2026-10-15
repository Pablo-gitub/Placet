"""
Agent sessions and the hook delivery channel: sessions register through their
hooks, a background waiter long-polls the session inbox and wakes the session.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import uvicorn

from backend.app.config import AgentSpec, PlacetConfig
from backend.app.database import Database
from backend.app.main import create_app
from backend.app.models import RequestCreate, RequestStatus
from tests.helpers import agent_client, human_client

WAITER = Path(__file__).resolve().parent.parent / "hooks" / "placet_waiter.py"

CONFIG = PlacetConfig(agents={
    "claude": AgentSpec(tmux_session="agent-claude", token="tok-claude",
                        allowed_targets=["gemini", "human"]),
    "gemini": AgentSpec(tmux_session="agent-gemini", token="tok-gemini",
                        allowed_targets=["claude", "human"]),
})


@pytest.fixture
def setup():
    db = Database(os.path.join(tempfile.mkdtemp(), "placet.db"))
    db.init_db()
    return create_app(db=db, config=CONFIG, bridge_token="tok-bridge"), db


async def _event(client, session_id, **body):
    return await client.post(f"/api/sessions/{session_id}/events", json=body)


def _approved(db, sender="claude", recipient="gemini", **extra):
    req = db.create_request(RequestCreate(sender_id=sender, recipient_id=recipient, prompt="Analizza", **extra))
    db.update_request_decision(req.id, RequestStatus.APPROVED, actor="human:p")
    return req


# --- Registration and lifecycle ----------------------------------------------------

@pytest.mark.asyncio
async def test_session_registers_and_tracks_turns(setup):
    app, db = setup
    async with agent_client(app, "tok-claude") as claude:
        await _event(claude, "s1", event="start", cwd="/home/p/proj", entrypoint="claude-desktop")
        assert db.get_session("s1").state == "idle"
        await _event(claude, "s1", event="prompt", title="Refactoring")
        session = db.get_session("s1")
        assert session.state == "busy" and session.title == "Refactoring"
        assert session.entrypoint == "claude-desktop" and session.cwd == "/home/p/proj"
        await _event(claude, "s1", event="stop")
        assert db.get_session("s1").state == "idle"
        await _event(claude, "s1", event="end")
    assert db.get_session("s1").ended_at is not None
    assert db.list_sessions() == []


@pytest.mark.asyncio
async def test_session_belongs_to_its_agent(setup):
    app, db = setup
    async with agent_client(app, "tok-claude") as claude:
        await _event(claude, "s1", event="start")
    async with agent_client(app, "tok-gemini") as gemini:
        assert (await _event(gemini, "s1", event="prompt")).status_code == 404
        assert (await gemini.get("/api/sessions/s1/inbox")).status_code == 404
    assert db.get_session("s1").agent_id == "claude"


@pytest.mark.asyncio
async def test_list_sessions(setup):
    app, _ = setup
    async with agent_client(app, "tok-claude") as claude:
        await _event(claude, "s1", event="start", cwd="/p")
    async with agent_client(app, "tok-gemini") as gemini:
        await _event(gemini, "g1", event="start")
        listed = (await gemini.get("/api/sessions")).json()
    assert {s["id"] for s in listed} == {"s1", "g1"}
    async with human_client(app) as human:
        only_claude = (await human.get("/api/sessions?agent_id=claude")).json()
    assert [s["id"] for s in only_claude] == ["s1"]
    async with agent_client(app, "tok-bridge") as bridge:
        assert (await bridge.get("/api/sessions")).status_code == 403


# --- Outcomes back to the session that asked ---------------------------------------

@pytest.mark.asyncio
async def test_outcome_goes_back_to_the_linked_session(setup):
    app, db = setup
    async with agent_client(app, "tok-claude") as claude:
        await _event(claude, "s1", event="start")
        created = (await claude.post("/api/requests", json={"recipient_id": "human", "prompt": "Ok?"})).json()
        linked = await _event(claude, "s1", event="tool", request_id=created["id"])
        assert linked.json()["linked"] is True
        assert (await claude.get("/api/sessions/s1/inbox")).status_code == 204  # not decided yet

    async with human_client(app) as human:
        await human.post(f"/api/requests/{created['id']}/decision", json={"status": "APPROVED"})

    async with agent_client(app, "tok-claude") as claude:
        message = (await claude.get("/api/sessions/s1/inbox")).json()
        assert message["kind"] == "outcome" and message["request_id"] == created["id"]
        assert "[Placet] Decisione dell'utente" in message["text"]
        assert "Questo non è un errore" in message["text"]
        assert (await claude.get("/api/sessions/s1/inbox")).status_code == 204  # only once
    delivered = [e for e in db.list_events(created["id"]) if e.event == "delivered"]
    assert delivered[0].details == {"via": "hook"}


@pytest.mark.asyncio
async def test_cannot_link_another_agents_request(setup):
    app, db = setup
    req = db.create_request(RequestCreate(sender_id="gemini", recipient_id="human", prompt="x"))
    async with agent_client(app, "tok-claude") as claude:
        await _event(claude, "s1", event="start")
        assert (await _event(claude, "s1", event="tool", request_id=req.id)).json()["linked"] is False


@pytest.mark.asyncio
async def test_busy_session_gets_nothing_until_turn_ends(setup):
    app, db = setup
    async with agent_client(app, "tok-gemini") as gemini:
        await _event(gemini, "g1", event="start")
        await _event(gemini, "g1", event="prompt")
        req = _approved(db)
        assert (await gemini.get("/api/sessions/g1/inbox")).status_code == 204
        await _event(gemini, "g1", event="stop")
        message = (await gemini.get("/api/sessions/g1/inbox")).json()
    assert message["kind"] == "injection" and message["request_id"] == req.id
    assert f"[Placet Request #{req.id} da claude]" in message["text"]
    assert "placet_ack" not in message["text"]  # the wake-up turn is the ack
    after = db.get_request(req.id)
    assert after.status == RequestStatus.DELIVERING and after.recipient_session_id == "g1"


@pytest.mark.asyncio
async def test_wake_up_turn_acknowledges_and_implicit_reply_per_session(setup):
    app, db = setup
    req = _approved(db)
    async with agent_client(app, "tok-gemini") as gemini:
        await _event(gemini, "g1", event="start")
        await gemini.get("/api/sessions/g1/inbox")
        await _event(gemini, "g1", event="prompt", request_id=req.id)
        assert db.get_request(req.id).status == RequestStatus.EXECUTING
        stop = await _event(gemini, "g1", event="stop", last_message="Analisi fatta")
    assert stop.json()["implicit_reply_for"] == req.id
    assert db.get_request(req.id).response.implicit is True


@pytest.mark.asyncio
async def test_long_poll_wakes_when_a_message_arrives(setup):
    import asyncio
    app, db = setup
    async with agent_client(app, "tok-gemini") as gemini:
        await _event(gemini, "g1", event="start")
        poll = asyncio.create_task(gemini.get("/api/sessions/g1/inbox?wait_seconds=10"))
        await asyncio.sleep(0.3)
        assert not poll.done()
        async with human_client(app) as human:
            created = db.create_request(RequestCreate(sender_id="claude", recipient_id="gemini", prompt="x"))
            await human.post(f"/api/requests/{created.id}/decision", json={"status": "APPROVED"})
        resp = await asyncio.wait_for(poll, timeout=3)
    assert resp.json()["request_id"] == created.id


# --- Choosing the session ------------------------------------------------------------

@pytest.mark.asyncio
async def test_targeted_session_only(setup):
    app, db = setup
    async with agent_client(app, "tok-gemini") as gemini:
        await _event(gemini, "g1", event="start")
        await _event(gemini, "g2", event="start")
    async with agent_client(app, "tok-claude") as claude:
        created = (await claude.post("/api/requests", json={
            "recipient_id": "gemini", "prompt": "x", "recipient_session_id": "g2",
        })).json()
    async with human_client(app) as human:
        await human.post(f"/api/requests/{created['id']}/decision", json={"status": "APPROVED"})
    async with agent_client(app, "tok-gemini") as gemini:
        assert (await gemini.get("/api/sessions/g1/inbox")).status_code == 204
        assert (await gemini.get("/api/sessions/g2/inbox")).json()["request_id"] == created["id"]


@pytest.mark.asyncio
async def test_human_picks_the_session_when_approving(setup):
    app, db = setup
    async with agent_client(app, "tok-gemini") as gemini:
        await _event(gemini, "g1", event="start")
        await _event(gemini, "g2", event="start")
    req = db.create_request(RequestCreate(sender_id="claude", recipient_id="gemini", prompt="x"))
    async with human_client(app) as human:
        wrong = await human.post(f"/api/requests/{req.id}/decision",
                                 json={"status": "APPROVED", "recipient_session_id": "nope"})
        assert wrong.status_code == 422
        ok = await human.post(f"/api/requests/{req.id}/decision",
                              json={"status": "APPROVED", "recipient_session_id": "g1"})
    assert ok.json()["recipient_session_id"] == "g1"


@pytest.mark.asyncio
async def test_target_session_must_belong_to_recipient(setup):
    app, _ = setup
    async with agent_client(app, "tok-claude") as claude:
        await _event(claude, "s1", event="start")
        resp = await claude.post("/api/requests", json={
            "recipient_id": "gemini", "prompt": "x", "recipient_session_id": "s1",
        })
    assert resp.status_code == 422


# --- tmux bridge vs hook channel -------------------------------------------------------

def _tmux_of(agent_id):
    return CONFIG.get_agent(agent_id).tmux_session


def test_reachable_hook_session_takes_injections_from_the_bridge(setup):
    _, db = setup
    req = _approved(db)
    assert db.tmux_work_filter([req], [], _tmux_of)[0] == [req]  # no session: tmux
    db.upsert_session("g1", "gemini")
    db.touch_waiter("g1")
    assert db.tmux_work_filter([req], [], _tmux_of)[0] == []      # listening: hook


def test_outcome_bound_to_desktop_session_never_goes_to_tmux(setup):
    _, db = setup
    db.upsert_session("s1", "claude", entrypoint="claude-desktop")
    req = db.create_request(RequestCreate(sender_id="claude", recipient_id="human", prompt="x"))
    db.link_sender_session(req.id, "s1", "claude")
    req = db.update_request_decision(req.id, RequestStatus.APPROVED, actor="human:p")
    # Waiter not listening (app closed): wait for the session, not agent-claude.
    assert db.tmux_work_filter([], [req], _tmux_of)[1] == []


def test_tmux_session_with_hooks_falls_back_to_tmux_when_not_listening(setup):
    _, db = setup
    db.upsert_session("t1", "claude", tmux_session="agent-claude")
    req = db.create_request(RequestCreate(sender_id="claude", recipient_id="human", prompt="x"))
    db.link_sender_session(req.id, "t1", "claude")
    req = db.update_request_decision(req.id, RequestStatus.APPROVED, actor="human:p")
    assert db.tmux_work_filter([], [req], _tmux_of)[1] == [req]
    db.touch_waiter("t1")
    assert db.tmux_work_filter([], [req], _tmux_of)[1] == []


def test_waiter_ttl(setup):
    _, db = setup
    db.upsert_session("s1", "claude")
    db.touch_waiter("s1")
    assert db.get_session("s1").reachable
    with db._get_connection() as conn:
        conn.execute("UPDATE sessions SET waiter_seen_at = ?",
                     ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),))
    assert not db.get_session("s1").reachable


# --- The real waiter process ----------------------------------------------------------

def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live_server(setup):
    app, db = setup
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", db
    server.should_exit = True
    thread.join(timeout=5)


def test_waiter_wakes_the_session_with_exit_code_2(live_server, tmp_path):
    url, db = live_server
    db.upsert_session("g1", "gemini")
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "PLACET_API_URL": url,
           "PLACET_API_TOKEN": "tok-gemini"}
    waiter = subprocess.Popen(
        [sys.executable, str(WAITER), "--agent", "gemini"], env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    waiter.stdin.write(json.dumps({"session_id": "g1", "cwd": str(tmp_path)}))
    waiter.stdin.close()
    time.sleep(1.0)
    assert waiter.poll() is None  # listening
    assert db.get_session("g1").reachable

    req = _approved(db)
    waiter.wait(timeout=15)
    err = waiter.stderr.read()
    assert waiter.returncode == 2
    assert f"[Placet Request #{req.id} da claude]" in err
    assert not list((tmp_path / ".cache" / "placet" / "waiters").glob("*.pid"))  # lock released


def test_second_waiter_for_the_same_session_steps_aside(live_server, tmp_path):
    url, db = live_server
    db.upsert_session("g1", "gemini")
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "PLACET_API_URL": url,
           "PLACET_API_TOKEN": "tok-gemini"}
    stdin = json.dumps({"session_id": "g1"})
    first = subprocess.Popen([sys.executable, str(WAITER), "--agent", "gemini"], env=env,
                             stdin=subprocess.PIPE, text=True)
    first.stdin.write(stdin)
    first.stdin.close()
    time.sleep(0.8)
    second = subprocess.run([sys.executable, str(WAITER), "--agent", "gemini"], env=env,
                            input=stdin, text=True, timeout=10)
    assert second.returncode == 0 and first.poll() is None
    first.kill()


def test_waiter_ignores_opted_out_projects(tmp_path):
    (tmp_path / ".placet-off").touch()
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "PLACET_API_TOKEN": "t",
           "PLACET_API_URL": "http://127.0.0.1:9"}
    res = subprocess.run([sys.executable, str(WAITER), "--agent", "gemini"], env=env, text=True,
                         input=json.dumps({"session_id": "x", "cwd": str(tmp_path)}), timeout=10)
    assert res.returncode == 0


@pytest.mark.asyncio
async def test_request_is_linked_to_the_only_busy_session(setup):
    """Server-side link: does not depend on the PostToolUse hook firing."""
    app, db = setup
    async with agent_client(app, "tok-claude") as claude:
        await _event(claude, "desk", event="start")
        await _event(claude, "tmux", event="start", tmux_session="agent-claude")
        await _event(claude, "desk", event="prompt")
        created = (await claude.post("/api/requests", json={"recipient_id": "human", "prompt": "x"})).json()
    assert created["sender_session_id"] == "desk"


@pytest.mark.asyncio
async def test_ambiguous_busy_sessions_are_not_guessed(setup):
    app, db = setup
    async with agent_client(app, "tok-claude") as claude:
        await _event(claude, "a", event="prompt")
        await _event(claude, "b", event="prompt")
        created = (await claude.post("/api/requests", json={"recipient_id": "human", "prompt": "x"})).json()
        assert created["sender_session_id"] is None
        await _event(claude, "b", event="tool", request_id=created["id"])
    assert db.get_request(created["id"]).sender_session_id == "b"


@pytest.mark.asyncio
async def test_same_agent_sessions_talk_without_self_delivery():
    """claude -> claude: the sender session never receives its own request."""
    db = Database(os.path.join(tempfile.mkdtemp(), "placet.db"))
    db.init_db()
    config = PlacetConfig(agents={"claude": AgentSpec(tmux_session="agent-claude", token="tok-claude",
                                                      allowed_targets=["claude", "human"])})
    app = create_app(db=db, config=config)
    async with agent_client(app, "tok-claude") as claude:
        await _event(claude, "A", event="start")
        await _event(claude, "B", event="start")
        await _event(claude, "A", event="prompt")
        created = (await claude.post("/api/requests", json={"recipient_id": "claude", "prompt": "Rivedi"})).json()
        assert created["sender_session_id"] == "A"
        self_target = await claude.post("/api/requests", json={
            "recipient_id": "claude", "prompt": "x", "recipient_session_id": "A"})
        assert self_target.status_code == 422
        await _event(claude, "A", event="stop")
    async with human_client(app) as human:
        await human.post(f"/api/requests/{created['id']}/decision", json={"status": "APPROVED"})
    async with agent_client(app, "tok-claude") as claude:
        assert (await claude.get("/api/sessions/A/inbox")).status_code == 204
        message = (await claude.get("/api/sessions/B/inbox")).json()
        assert message["request_id"] == created["id"]
        await _event(claude, "B", event="prompt", request_id=created["id"])
        await claude.post(f"/api/requests/{created['id']}/reply", json={"content": "Rivisto"})
        await _event(claude, "B", event="stop")
        outcome = (await claude.get("/api/sessions/A/inbox")).json()
    assert outcome["kind"] == "outcome" and "Rivisto" in outcome["text"]


def test_outcome_tells_the_sender_its_prompt_was_edited(setup):
    from backend.app import envelopes
    _, db = setup
    req = db.create_request(RequestCreate(sender_id="claude", recipient_id="gemini", prompt="Scrivi un haiku"))
    db.update_request_decision(req.id, RequestStatus.APPROVED, edited_prompt="Scrivi ciao", actor="human:p")
    db.claim_injection(req.id)
    done = db.record_reply(req.id, "gemini", "ciao")
    text = envelopes.outcome(done, channel="hook")
    assert "l'utente ha modificato il tuo prompt" in text and "Scrivi ciao" in text


@pytest.mark.asyncio
async def test_sending_to_an_unreachable_session_warns(setup):
    app, db = setup
    db.upsert_session("g-old", "gemini")  # registered, no waiter listening
    async with agent_client(app, "tok-claude") as claude:
        resp = await claude.post("/api/requests", json={
            "recipient_id": "gemini", "prompt": "x", "recipient_session_id": "g-old"})
    assert resp.status_code == 201 and "not reachable" in resp.json()["warning"]
