import os
import tempfile
import pytest
from datetime import datetime, timedelta, timezone
from httpx import AsyncClient, ASGITransport

from tests.helpers import human_client

from backend.app.main import create_app
from backend.app.database import Database
from backend.app.config import load_agents_config
from backend.app.models import RequestCreate, RequestStatus, QuestionType, RequestDecision


@pytest.fixture
def edge_setup():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    fd_md, md_path = tempfile.mkstemp(suffix=".md")
    os.close(fd_md)
    with open(md_path, "w") as f:
        f.write("""---
agents:
  claude:
    name: "Claude Code"
    tmux_session: "agent-claude"
    token: "secret-token-claude"
    allowed_targets: ["gemini", "human"]
    default_timeout_seconds: 60
  gemini:
    name: "Gemini CLI"
    tmux_session: "agent-gemini"
    token: "secret-token-gemini"
    allowed_targets: ["claude", "human"]
rules:
  claude -> gemini:
    require_approval: true
---
# Agents Doc
""")

    db = Database(db_path)
    db.init_db()
    config = load_agents_config(md_path)
    app = create_app(db=db, config=config)

    yield app, db, config

    if os.path.exists(db_path):
        os.remove(db_path)
    if os.path.exists(md_path):
        os.remove(md_path)


# Test 1: Invalid state transition - Cannot decide on already APPROVED or REJECTED request (409 Conflict)
@pytest.mark.asyncio
async def test_cannot_decide_twice_or_on_terminal_state(edge_setup):
    app, db, config = edge_setup
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create request
        resp = await client.post("/api/requests", json={
            "sender_id": "claude",
            "recipient_id": "gemini",
            "prompt": "Test prompt 1",
        }, headers={"Authorization": "Bearer secret-token-claude"})
        assert resp.status_code == 201
        req_id = resp.json()["id"]

        async with human_client(app) as human:
            # First decision -> 200 OK
            dec1 = await human.post(f"/api/requests/{req_id}/decision", json={"status": "APPROVED"})
            assert dec1.status_code == 200
            assert dec1.json()["status"] == "APPROVED"

            # Second decision -> 409 Conflict (cannot decide an already decided request)
            dec2 = await human.post(f"/api/requests/{req_id}/decision", json={"status": "REJECTED"})
        assert dec2.status_code == 409
        assert "not in PENDING state" in dec2.json()["detail"]


# Test 2: Cannot reply to a request that is not APPROVED or EXECUTING (409 Conflict)
@pytest.mark.asyncio
async def test_cannot_reply_to_pending_or_cancelled_request(edge_setup):
    app, db, config = edge_setup
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create request (in PENDING state)
        resp = await client.post("/api/requests", json={
            "sender_id": "claude",
            "recipient_id": "gemini",
            "prompt": "Test prompt 2",
        }, headers={"Authorization": "Bearer secret-token-claude"})
        req_id = resp.json()["id"]

        # Attempt to reply while still PENDING (not yet approved/executing)
        reply_resp = await client.post(f"/api/requests/{req_id}/reply", json={
            "responder_id": "gemini",
            "content": "Premature reply",
        }, headers={"Authorization": "Bearer secret-token-gemini"})
        assert reply_resp.status_code == 409
        assert "not in APPROVED or EXECUTING state" in reply_resp.json()["detail"]


# Test 3: Cannot cancel an already APPROVED, EXECUTING, or COMPLETED request
@pytest.mark.asyncio
async def test_cannot_cancel_non_pending_request(edge_setup):
    app, db, config = edge_setup
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/requests", json={
            "sender_id": "claude",
            "recipient_id": "gemini",
            "prompt": "Test prompt 3",
        }, headers={"Authorization": "Bearer secret-token-claude"})
        req_id = resp.json()["id"]

        async with human_client(app) as human:
            # Approve
            await human.post(f"/api/requests/{req_id}/decision", json={"status": "APPROVED"})

            # Try to cancel
            cancel_resp = await human.delete(f"/api/requests/{req_id}")
        assert cancel_resp.status_code == 409


# Test 4: Token Authentication - reject invalid or missing token when token is defined in AGENTS.md
@pytest.mark.asyncio
async def test_token_authentication_enforcement(edge_setup):
    app, db, config = edge_setup
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Request with wrong token
        resp1 = await client.post("/api/requests", json={
            "sender_id": "claude",
            "recipient_id": "gemini",
            "prompt": "Imposter prompt",
        }, headers={"Authorization": "Bearer wrong-token"})
        assert resp1.status_code == 401
        assert "Invalid agent token" in resp1.json()["detail"]

        # Request with missing token
        resp2 = await client.post("/api/requests", json={
            "sender_id": "claude",
            "recipient_id": "gemini",
            "prompt": "No token prompt",
        })
        assert resp2.status_code == 401

        # Request with valid token
        resp3 = await client.post("/api/requests", json={
            "sender_id": "claude",
            "recipient_id": "gemini",
            "prompt": "Authorized prompt",
        }, headers={"Authorization": "Bearer secret-token-claude"})
        assert resp3.status_code == 201


# Test 5: Complex / Special payloads - Unicode, emojis, newlines, markdown, code blocks
@pytest.mark.asyncio
async def test_complex_and_multiline_payloads(edge_setup):
    app, db, config = edge_setup
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        complex_prompt = (
            "🚀 Verifica questa funzione Python con caratteri speciali:\n"
            "```python\n"
            "def test_symbols(x='\"quotes\"', y=['a', 'b'], z={'key': True}):\n"
            "    # Commento con caratteri speciali: é, à, ò, ù, €\n"
            "    return f'Result: {x} & {y} | $HOME'\n"
            "```\n"
            "Cosa ne pensi?"
        )
        resp = await client.post("/api/requests", json={
            "sender_id": "claude",
            "recipient_id": "human",
            "question_type": "single_choice",
            "prompt": complex_prompt,
            "context": "Verifica robustezza escape",
            "options": ["Opzione 1: € / $", "Opzione 2: 'virgolette' e \"doppie\""],
        }, headers={"Authorization": "Bearer secret-token-claude"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["prompt"] == complex_prompt
        assert "€" in data["options"][0]
        assert "\"doppie\"" in data["options"][1]


# Test 6: Stale request expiration mechanism
def test_database_expire_stale_requests(edge_setup):
    app, db, config = edge_setup
    req = db.create_request(RequestCreate(
        sender_id="claude",
        recipient_id="human",
        prompt="Expiring request",
        expires_in_seconds=10,
    ))
    assert req.status == RequestStatus.PENDING
    assert req.expires_at is not None

    # Not expired yet
    assert db.expire_stale_requests() == []

    # Move expires_at into the past
    past_time = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    with db._get_connection() as conn:
        conn.execute("UPDATE requests SET expires_at = ? WHERE id = ?", (past_time, req.id))

    # Now expire_stale_requests should mark it as EXPIRED
    assert db.expire_stale_requests() == [req.id]

    updated = db.get_request(req.id)
    assert updated.status == RequestStatus.EXPIRED
