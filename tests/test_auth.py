"""
Authorization rules: agents are identified only by their token, the human only
by the Tailscale identity header on the human listener.
"""
import os
import tempfile

import pytest

from backend.app.config import load_agents_config
from backend.app.database import Database
from backend.app.main import create_app
from backend.app.models import RequestCreate, RequestStatus
from tests.helpers import HUMAN_LOGIN, agent_client, human_client

AGENTS_MD = """---
agents:
  claude:
    tmux_session: "agent-claude"
    token: "tok-claude"
    allowed_targets: ["gemini", "codex", "human"]
  gemini:
    tmux_session: "agent-gemini"
    token: "tok-gemini"
    allowed_targets: ["claude", "human"]
  codex:
    tmux_session: "agent-codex"
    token: "tok-codex"
    allowed_targets: ["human"]
  notoken:
    tmux_session: "agent-notoken"
    token: ""
    allowed_targets: ["human"]
---
"""


@pytest.fixture
def setup():
    tmpdir = tempfile.mkdtemp()
    md_path = os.path.join(tmpdir, "AGENTS.md")
    with open(md_path, "w") as f:
        f.write(AGENTS_MD)
    db = Database(os.path.join(tmpdir, "placet.db"))
    db.init_db()
    app = create_app(db=db, config=load_agents_config(md_path))
    return app, db


def _approved_request(db, sender="claude", recipient="gemini"):
    req = db.create_request(RequestCreate(sender_id=sender, recipient_id=recipient, prompt="x"))
    db.update_request_decision(req.id, RequestStatus.APPROVED)
    return req


@pytest.mark.asyncio
async def test_forged_tailscale_header_ignored_on_agent_listener(setup):
    app, db = setup
    req = db.create_request(RequestCreate(sender_id="claude", recipient_id="human", prompt="x"))
    async with agent_client(app) as client:
        resp = await client.post(
            f"/api/requests/{req.id}/decision",
            json={"status": "APPROVED"},
            headers={"Tailscale-User-Login": HUMAN_LOGIN},
        )
        assert resp.status_code == 401
    assert db.get_request(req.id).status == RequestStatus.PENDING


@pytest.mark.asyncio
async def test_human_listener_rejects_unknown_or_missing_login(setup):
    app, _ = setup
    async with human_client(app, login="intruso@example.com") as human:
        assert (await human.get("/api/requests")).status_code == 403
    async with human_client(app, login=None) as human:
        assert (await human.get("/api/requests")).status_code == 401


@pytest.mark.asyncio
async def test_no_allowed_users_means_no_human_access(setup, monkeypatch):
    monkeypatch.setenv("PLACET_ALLOWED_USERS", "")
    _, db = setup
    app = create_app(db=db, config=load_agents_config(None))
    async with human_client(app) as human:
        assert (await human.get("/api/requests")).status_code == 403


@pytest.mark.asyncio
async def test_agent_cannot_decide_list_or_stream(setup):
    app, db = setup
    req = db.create_request(RequestCreate(sender_id="claude", recipient_id="human", prompt="x"))
    async with agent_client(app, "tok-claude") as client:
        resp = await client.post(f"/api/requests/{req.id}/decision", json={"status": "APPROVED"})
        assert resp.status_code == 403
        assert (await client.get("/api/requests")).status_code == 403
        assert (await client.get("/api/events")).status_code == 403
    assert db.get_request(req.id).status == RequestStatus.PENDING


@pytest.mark.asyncio
async def test_human_cannot_create_requests(setup):
    app, _ = setup
    async with human_client(app) as human:
        resp = await human.post("/api/requests", json={"recipient_id": "gemini", "prompt": "x"})
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_sender_is_derived_from_token(setup):
    app, _ = setup
    async with agent_client(app, "tok-claude") as client:
        resp = await client.post("/api/requests", json={"recipient_id": "human", "prompt": "x"})
        assert resp.status_code == 201
        assert resp.json()["sender_id"] == "claude"

        spoof = await client.post(
            "/api/requests", json={"sender_id": "gemini", "recipient_id": "human", "prompt": "x"}
        )
        assert spoof.status_code == 403


@pytest.mark.asyncio
async def test_agent_without_token_cannot_authenticate(setup):
    app, _ = setup
    async with agent_client(app, "") as client:
        resp = await client.post(
            "/api/requests", json={"sender_id": "notoken", "recipient_id": "human", "prompt": "x"}
        )
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_only_recipient_can_reply(setup):
    app, db = setup
    req = _approved_request(db)
    async with agent_client(app, "tok-claude") as sender:
        resp = await sender.post(f"/api/requests/{req.id}/reply", json={"content": "fake"})
        assert resp.status_code == 403
    async with agent_client(app, "tok-codex") as outsider:
        resp = await outsider.post(f"/api/requests/{req.id}/reply", json={"content": "fake"})
        assert resp.status_code == 404
    async with agent_client(app, "tok-gemini") as recipient:
        spoof = await recipient.post(
            f"/api/requests/{req.id}/reply", json={"content": "x", "responder_id": "codex"}
        )
        assert spoof.status_code == 403
        ok = await recipient.post(f"/api/requests/{req.id}/reply", json={"content": "vero"})
        assert ok.status_code == 200
        assert ok.json()["response"]["responder_id"] == "gemini"


@pytest.mark.asyncio
async def test_cancel_permissions(setup):
    app, db = setup
    r1 = db.create_request(RequestCreate(sender_id="claude", recipient_id="gemini", prompt="x"))
    r2 = db.create_request(RequestCreate(sender_id="claude", recipient_id="gemini", prompt="y"))
    async with agent_client(app, "tok-codex") as outsider:
        assert (await outsider.delete(f"/api/requests/{r1.id}")).status_code == 404
    async with agent_client(app, "tok-gemini") as recipient:
        assert (await recipient.delete(f"/api/requests/{r1.id}")).status_code == 403
    async with agent_client(app, "tok-claude") as sender:
        assert (await sender.delete(f"/api/requests/{r1.id}")).status_code == 200
    async with human_client(app) as human:
        assert (await human.delete(f"/api/requests/{r2.id}")).status_code == 200


@pytest.mark.asyncio
async def test_agent_sees_only_own_requests(setup):
    app, db = setup
    req = db.create_request(RequestCreate(sender_id="claude", recipient_id="gemini", prompt="x"))
    async with agent_client(app, "tok-codex") as outsider:
        assert (await outsider.get(f"/api/requests/{req.id}")).status_code == 404
    async with agent_client(app, "tok-gemini") as recipient:
        assert (await recipient.get(f"/api/requests/{req.id}")).status_code == 200
    async with human_client(app) as human:
        assert (await human.get(f"/api/requests/{req.id}")).status_code == 200


@pytest.mark.asyncio
async def test_no_cors_headers_by_default(setup):
    app, _ = setup
    async with human_client(app) as human:
        resp = await human.get("/api/requests", headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in resp.headers


@pytest.mark.asyncio
async def test_cors_only_for_configured_origins(setup):
    _, db = setup
    app = create_app(
        db=db, config=load_agents_config(None),
        cors_origins=["http://localhost:5173"],
    )
    async with human_client(app) as human:
        ok = await human.get("/api/requests", headers={"Origin": "http://localhost:5173"})
        assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
        bad = await human.get("/api/requests", headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in bad.headers
