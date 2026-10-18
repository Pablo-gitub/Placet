"""
Regression tests for the request lifecycle: long-polling, expiration
independent from the wait time, decision validation and list ordering.
"""
import asyncio
import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.config import load_agents_config
from backend.app.database import Database
from backend.app.main import create_app
from backend.app.models import RequestCreate, RequestStatus
from tests.helpers import agent_client, human_client


@pytest.fixture(autouse=True)
def short_minimum_wait(monkeypatch):
    """Questions to the user wait at least a minute (RF-09): one second here."""
    monkeypatch.setattr("backend.app.main.MIN_HUMAN_WAIT_SECONDS", 1)

AUTH_CLAUDE = {"Authorization": "Bearer tok-claude"}
AUTH_GEMINI = {"Authorization": "Bearer tok-gemini"}


@pytest.fixture
def setup():
    tmpdir = tempfile.mkdtemp()
    md_path = os.path.join(tmpdir, "AGENTS.md")
    with open(md_path, "w") as f:
        f.write("""---
agents:
  claude:
    tmux_session: "agent-claude"
    token: "tok-claude"
    allowed_targets: ["gemini", "human"]
  gemini:
    tmux_session: "agent-gemini"
    token: "tok-gemini"
    allowed_targets: ["claude", "human"]
rules:
  claude -> gemini:
    require_approval: true
---
""")
    db = Database(os.path.join(tmpdir, "placet.db"))
    db.init_db()
    app = create_app(db=db, config=load_agents_config(md_path))
    yield app, db


@pytest.mark.asyncio
async def test_long_poll_waits_for_human_decision(setup):
    app, db = setup
    async with agent_client(app) as client, human_client(app) as human:
        create = asyncio.create_task(client.post(
            "/api/requests?wait_seconds=10",
            json={"sender_id": "claude", "recipient_id": "human", "prompt": "Ok?"},
            headers=AUTH_CLAUDE,
        ))
        await asyncio.sleep(0.3)
        # The caller must still be waiting: no decision yet.
        assert not create.done()

        pending = db.list_requests(status=RequestStatus.PENDING)
        assert len(pending) == 1
        resp = await human.post(
            f"/api/requests/{pending[0].id}/decision", json={"status": "APPROVED"}
        )
        assert resp.status_code == 200

        result = await asyncio.wait_for(create, timeout=2)
        assert result.json()["status"] == "APPROVED"


@pytest.mark.asyncio
async def test_long_poll_agent_request_waits_for_reply_not_approval(setup):
    app, db = setup
    async with agent_client(app) as client, human_client(app) as human:
        create = asyncio.create_task(client.post(
            "/api/requests?wait_seconds=10",
            json={"sender_id": "claude", "recipient_id": "gemini", "prompt": "Analizza"},
            headers=AUTH_CLAUDE,
        ))
        await asyncio.sleep(0.3)
        req_id = db.list_requests()[0].id

        await human.post(f"/api/requests/{req_id}/decision", json={"status": "APPROVED"})
        await asyncio.sleep(0.3)
        # Approval alone does not settle an agent-to-agent request.
        assert not create.done()

        resp = await client.post(
            f"/api/requests/{req_id}/reply",
            json={"responder_id": "gemini", "content": "Fatto"},
            headers=AUTH_GEMINI,
        )
        assert resp.status_code == 200

        result = (await asyncio.wait_for(create, timeout=2)).json()
        assert result["status"] == "COMPLETED"
        assert result["response"] is None
        await human.post(f"/api/requests/{req_id}/return-decision", json={"approve": True})
        approved = await client.get(f"/api/requests/{req_id}", headers=AUTH_CLAUDE)
        assert approved.json()["response"]["content"] == "Fatto"


@pytest.mark.asyncio
async def test_long_poll_timeout_returns_pending(setup):
    app, _ = setup
    async with agent_client(app) as client, human_client(app) as human:
        resp = await client.post(
            "/api/requests?wait_seconds=1",
            json={"sender_id": "claude", "recipient_id": "human", "prompt": "Ok?"},
            headers=AUTH_CLAUDE,
        )
        assert resp.json()["status"] == "PENDING"


@pytest.mark.asyncio
async def test_request_survives_wait_timeout_and_can_be_decided_later(setup):
    app, _ = setup
    async with agent_client(app) as client, human_client(app) as human:
        created = (await client.post(
            "/api/requests?wait_seconds=1",
            json={"sender_id": "claude", "recipient_id": "human", "prompt": "Ok?"},
            headers=AUTH_CLAUDE,
        )).json()
        assert created["status"] == "PENDING"

        await asyncio.sleep(0.2)
        await human.get("/api/requests")  # used to trigger expiration
        resp = await human.post(
            f"/api/requests/{created['id']}/decision", json={"status": "APPROVED"}
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "APPROVED"


@pytest.mark.asyncio
async def test_get_request_long_poll(setup):
    app, db = setup
    req = db.create_request(RequestCreate(sender_id="claude", recipient_id="human", prompt="Ok?"))
    async with agent_client(app) as client, human_client(app) as human:
        poll = asyncio.create_task(human.get(f"/api/requests/{req.id}?wait_seconds=10"))
        await asyncio.sleep(0.3)
        assert not poll.done()
        await human.post(f"/api/requests/{req.id}/decision", json={"status": "REJECTED"})
        result = await asyncio.wait_for(poll, timeout=2)
        assert result.json()["status"] == "REJECTED"


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_status", ["COMPLETED", "EXECUTING", "EXPIRED", "CANCELLED", "PENDING"])
async def test_decision_rejects_non_human_statuses(setup, bad_status):
    app, db = setup
    req = db.create_request(RequestCreate(sender_id="claude", recipient_id="human", prompt="Ok?"))
    async with agent_client(app) as client, human_client(app) as human:
        resp = await human.post(f"/api/requests/{req.id}/decision", json={"status": bad_status})
        assert resp.status_code == 422
    assert db.get_request(req.id).status == RequestStatus.PENDING


@pytest.mark.asyncio
async def test_decision_on_expired_request_is_conflict(setup):
    app, db = setup
    req = db.create_request(RequestCreate(
        sender_id="claude", recipient_id="human", prompt="Ok?", expires_in_seconds=10
    ))
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with db._get_connection() as conn:
        conn.execute("UPDATE requests SET expires_at = ? WHERE id = ?", (past, req.id))
    async with agent_client(app) as client, human_client(app) as human:
        resp = await human.post(f"/api/requests/{req.id}/decision", json={"status": "APPROVED"})
        assert resp.status_code == 409
    assert db.get_request(req.id).status == RequestStatus.EXPIRED


def test_expiration_covers_approved_and_executing(setup):
    _, db = setup
    ids = []
    for target_status in (RequestStatus.APPROVED, RequestStatus.EXECUTING):
        req = db.create_request(RequestCreate(
            sender_id="claude", recipient_id="gemini", prompt="x", expires_in_seconds=10
        ))
        db.update_request_decision(req.id, RequestStatus.APPROVED)
        if target_status == RequestStatus.EXECUTING:
            db.claim_injection(req.id)
            db.confirm_injection(req.id, actor="agent:gemini", via="mcp")
        ids.append(req.id)
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with db._get_connection() as conn:
        conn.execute("UPDATE requests SET expires_at = ?", (past,))
    assert db.expire_stale_requests() == ids


def test_executing_handoff_keeps_human_decision(setup):
    _, db = setup
    req = db.create_request(RequestCreate(sender_id="claude", recipient_id="gemini", prompt="x"))
    db.update_request_decision(req.id, RequestStatus.APPROVED, edited_prompt="y")
    db.claim_injection(req.id)
    executing = db.confirm_injection(req.id, actor="agent:gemini", via="hook")
    assert executing.status == RequestStatus.EXECUTING
    assert executing.edited_prompt == "y"


def test_list_requests_newest_first_beyond_limit(setup):
    _, db = setup
    for i in range(55):
        db.create_request(RequestCreate(sender_id="claude", recipient_id="human", prompt=str(i)))
    newest = db.list_requests(limit=50)
    assert newest[0].prompt == "54"
    assert len(newest) == 50
    oldest = db.list_requests(limit=50, oldest_first=True)
    assert oldest[0].prompt == "0"


@pytest.mark.asyncio
async def test_questions_to_the_user_wait_a_minimum_and_explain_what_follows(setup, monkeypatch):
    monkeypatch.setattr("backend.app.main.MIN_HUMAN_WAIT_SECONDS", 2)
    app, _ = setup
    async with agent_client(app) as client:
        loop = asyncio.get_running_loop()
        started = loop.time()
        resp = (await client.post(
            "/api/requests?wait_seconds=1",
            json={"sender_id": "claude", "recipient_id": "human", "prompt": "Ok?", "context": "x"},
            headers=AUTH_CLAUDE,
        )).json()
        assert loop.time() - started >= 1.9
        assert resp["status"] == "PENDING" and resp["wait_seconds"] == 2
        assert "[Placet" in resp["note"] and "Termina il turno" in resp["note"]
