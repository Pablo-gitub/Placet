"""RF-13: every state change of a request is recorded with who did it and when."""
import os
import tempfile
from datetime import datetime, timezone

import pytest

from backend.app.config import AgentSpec, PlacetConfig
from backend.app.database import Database
from backend.app.main import create_app
from backend.app.models import RequestCreate, RequestStatus
from tests.helpers import HUMAN_LOGIN, agent_client, human_client

CONFIG = PlacetConfig(
    agents={
        "claude": AgentSpec(tmux_session="agent-claude", token="tok-claude",
                            allowed_targets=["gemini", "human"]),
        "gemini": AgentSpec(tmux_session="agent-gemini", token="tok-gemini",
                            allowed_targets=["claude", "human"]),
        "codex": AgentSpec(tmux_session="agent-codex", token="tok-codex",
                           allowed_targets=["human"]),
    }
)


@pytest.fixture
def setup():
    db = Database(os.path.join(tempfile.mkdtemp(), "placet.db"))
    db.init_db()
    return create_app(db=db, config=CONFIG), db


def _events(db, req_id):
    return [(e.actor, e.event, e.details) for e in db.list_events(req_id)]


@pytest.mark.asyncio
async def test_full_agent_to_agent_timeline(setup):
    app, db = setup
    async with agent_client(app, "tok-claude") as claude:
        req = (await claude.post("/api/requests", json={"recipient_id": "gemini", "prompt": "Analizza"})).json()
    async with human_client(app) as human:
        await human.post(f"/api/requests/{req['id']}/decision",
                         json={"status": "APPROVED", "edited_prompt": "Analizza bene"})
    db.claim_injection(req["id"], actor="bridge")
    db.confirm_injection(req["id"], actor="agent:gemini", via="hook")
    async with agent_client(app, "tok-gemini") as gemini:
        await gemini.post(f"/api/requests/{req['id']}/reply", json={"content": "Fatto"})
    async with human_client(app) as human:
        await human.post(f"/api/requests/{req['id']}/return-decision", json={"approve": True})
    async with agent_client(app, "tok-claude") as claude:
        await claude.get(f"/api/requests/{req['id']}")

    assert _events(db, req["id"]) == [
        ("agent:claude", "created", {"question_type": "prompt_review",
                                     "expires_at": db.get_request(req["id"]).expires_at.isoformat()}),
        (f"human:{HUMAN_LOGIN}", "decided", {"status": "APPROVED", "delivery_mode": "queue", "edited_prompt": "Analizza bene"}),
        ("bridge", "injection_claimed", {"attempt": 1}),
        ("agent:gemini", "acked", {"via": "hook"}),
        ("agent:gemini", "replied", {"length": 5}),
        (f"human:{HUMAN_LOGIN}", "return_decided", {"approve": True, "edited_response": None}),
        ("agent:claude", "delivered", {"via": "api"}),
    ]


@pytest.mark.asyncio
async def test_choice_answer_details_are_logged(setup):
    app, db = setup
    async with agent_client(app, "tok-claude") as claude:
        req = (await claude.post("/api/requests", json={
            "recipient_id": "human", "question_type": "multi_choice",
            "prompt": "Tag?", "options": ["a", "b"],
        })).json()
    async with human_client(app) as human:
        await human.post(f"/api/requests/{req['id']}/decision", json={
            "status": "APPROVED", "selected_options": ["a"], "custom_text": "c",
        })
    decided = [e for e in db.list_events(req["id"]) if e.event == "decided"][0]
    assert decided.details == {"status": "APPROVED", "selected_options": ["a"], "custom_text": "c"}


def test_auto_routing_actor(setup):
    _, db = setup
    req = db.create_request(RequestCreate(sender_id="gemini", recipient_id="claude", prompt="x"))
    db.update_request_decision(req.id, RequestStatus.APPROVED, actor="system:auto-routing")
    assert _events(db, req.id)[-1][:2] == ("system:auto-routing", "decided")


@pytest.mark.asyncio
async def test_cancel_and_expiry_are_logged(setup):
    app, db = setup
    async with agent_client(app, "tok-claude") as claude:
        a = (await claude.post("/api/requests", json={"recipient_id": "human", "prompt": "a"})).json()
        b = (await claude.post("/api/requests", json={
            "recipient_id": "human", "prompt": "b", "expires_in_seconds": 10,
        })).json()
        await claude.delete(f"/api/requests/{a['id']}")
    with db._get_connection() as conn:
        conn.execute("UPDATE requests SET expires_at = ? WHERE id = ?",
                     (datetime.now(timezone.utc).isoformat(), b["id"]))
    db.expire_stale_requests()

    assert _events(db, a["id"])[-1][:2] == ("agent:claude", "cancelled")
    assert _events(db, b["id"])[-1][:2] == ("system", "expired")


def test_failed_delivery_is_logged(setup):
    _, db = setup
    req = db.create_request(RequestCreate(sender_id="claude", recipient_id="human", prompt="x"))
    db.update_request_decision(req.id, RequestStatus.APPROVED, actor="human:p")
    assert db.mark_delivered(req.id, actor="bridge", via="tmux")
    db.clear_delivered(req.id, actor="bridge")
    assert [e[1] for e in _events(db, req.id)][-2:] == ["delivered", "delivery_failed"]


@pytest.mark.asyncio
async def test_events_endpoint_permissions(setup):
    app, db = setup
    async with agent_client(app, "tok-claude") as claude:
        req = (await claude.post("/api/requests", json={"recipient_id": "gemini", "prompt": "x"})).json()
        assert (await claude.get(f"/api/requests/{req['id']}/events")).status_code == 200
    async with agent_client(app, "tok-codex") as outsider:
        assert (await outsider.get(f"/api/requests/{req['id']}/events")).status_code == 404
    async with human_client(app) as human:
        resp = await human.get(f"/api/requests/{req['id']}/events")
        assert resp.status_code == 200
        assert resp.json()[0]["event"] == "created"
