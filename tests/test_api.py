import os
import tempfile
import pytest
from httpx import AsyncClient, ASGITransport
from backend.app.main import create_app
from backend.app.database import Database
from backend.app.config import load_agents_config
from backend.app.models import RequestStatus, QuestionType


@pytest.fixture
def test_setup():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    
    # Create temporary AGENTS.md
    fd_md, md_path = tempfile.mkstemp(suffix=".md")
    os.close(fd_md)
    with open(md_path, "w") as f:
        f.write("""---
agents:
  claude:
    name: "Claude Code"
    tmux_session: "agent-claude"
    token: "token-claude"
    allowed_targets: ["gemini", "human"]
  gemini:
    name: "Gemini CLI"
    tmux_session: "agent-gemini"
    token: "token-gemini"
    allowed_targets: ["claude", "human"]
rules:
  claude -> gemini:
    require_approval: true
  gemini -> claude:
    require_approval: false
---
# Agents Doc
""")

    db = Database(db_path)
    db.init_db()
    config = load_agents_config(md_path)
    
    app = create_app(db=db, config=config, ntfy_topic="test-topic")
    
    yield app, db, config
    
    if os.path.exists(db_path):
        os.remove(db_path)
    if os.path.exists(md_path):
        os.remove(md_path)


@pytest.mark.asyncio
async def test_api_list_agents(test_setup):
    app, db, config = test_setup
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/agents")
        assert response.status_code == 200
        data = response.json()
        assert "claude" in data["agents"]
        assert "gemini" in data["agents"]
        assert data["agents"]["claude"]["tmux_session"] == "agent-claude"


@pytest.mark.asyncio
async def test_api_create_and_decide_request(test_setup):
    app, db, config = test_setup
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create a request requiring approval
        payload = {
            "sender_id": "claude",
            "recipient_id": "gemini",
            "question_type": "prompt_review",
            "prompt": "Test prompt da verificare",
            "context": "Context di test",
            "options": [],
        }
        resp = await client.post(
            "/api/requests",
            json=payload,
            headers={"Authorization": "Bearer token-claude"},
        )
        assert resp.status_code == 201
        data = resp.json()
        req_id = data["id"]
        assert data["status"] == RequestStatus.PENDING.value

        # Make decision (approval with prompt modification)
        decision = {
            "status": "APPROVED",
            "edited_prompt": "Prompt modificato da Paolo",
        }
        dec_resp = await client.post(f"/api/requests/{req_id}/decision", json=decision)
        assert dec_resp.status_code == 200
        dec_data = dec_resp.json()
        assert dec_data["status"] == RequestStatus.APPROVED.value
        assert dec_data["edited_prompt"] == "Prompt modificato da Paolo"

        # Record reply from recipient
        reply_payload = {
            "responder_id": "gemini",
            "content": "Risposta completata con successo",
        }
        rep_resp = await client.post(f"/api/requests/{req_id}/reply", json=reply_payload)
        assert rep_resp.status_code == 200
        rep_data = rep_resp.json()
        assert rep_data["status"] == RequestStatus.COMPLETED.value
        assert rep_data["response"]["content"] == "Risposta completata con successo"


@pytest.mark.asyncio
async def test_api_auto_routing(test_setup):
    app, db, config = test_setup
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # gemini -> claude has require_approval: false in test_setup config
        payload = {
            "sender_id": "gemini",
            "recipient_id": "claude",
            "question_type": "prompt_review",
            "prompt": "Domanda rapida senza approvazione",
        }
        resp = await client.post(
            "/api/requests",
            json=payload,
            headers={"Authorization": "Bearer token-gemini"},
        )
        assert resp.status_code == 201
        data = resp.json()
        # Should be auto-approved!
        assert data["status"] == RequestStatus.APPROVED.value



@pytest.mark.asyncio
async def test_api_unauthorized_route(test_setup):
    app, db, config = test_setup
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # claude is only allowed to target gemini and human in test_setup
        payload = {
            "sender_id": "claude",
            "recipient_id": "unknown_bot",
            "prompt": "Fail",
        }
        resp = await client.post("/api/requests", json=payload)
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_pwa_index_served(test_setup):
    app, db, config = test_setup
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "Placet" in resp.text

