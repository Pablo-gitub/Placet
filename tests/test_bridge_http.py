"""
The bridge in production talks to the backend only over REST, with its own
token, and can do nothing beyond its job.
"""
import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from backend.app.config import AgentSpec, PlacetConfig
from backend.app.database import Database
from backend.app.main import create_app
from backend.app.models import RequestCreate, RequestStatus
from bridge.bridge_daemon import BridgeDaemon
from bridge.stores import HttpStore
from bridge.tmux_controller import TmuxController
from tests.helpers import agent_client, human_client

CONFIG = PlacetConfig(
    agents={
        "claude": AgentSpec(tmux_session="agent-claude", token="tok-claude",
                            allowed_targets=["gemini", "human"]),
        "gemini": AgentSpec(tmux_session="agent-gemini", token="tok-gemini",
                            allowed_targets=["claude", "human"]),
    }
)
BRIDGE_TOKEN = "tok-bridge"


@pytest.fixture
def setup():
    db = Database(os.path.join(tempfile.mkdtemp(), "placet.db"))
    db.init_db()
    app = create_app(db=db, config=CONFIG, bridge_token=BRIDGE_TOKEN)
    tmux = MagicMock(spec=TmuxController)
    tmux.session_exists.return_value = True
    tmux.is_session_idle.return_value = True
    tmux.send_keys.return_value = True
    store = HttpStore(token=BRIDGE_TOKEN, client=TestClient(app))
    return app, db, BridgeDaemon(store=store, tmux=tmux), tmux


def _age(db, req_id, seconds=30):
    past = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
    with db._get_connection() as conn:
        conn.execute("UPDATE requests SET updated_at = ? WHERE id = ?", (past, req_id))


def test_injection_and_delivery_over_rest(setup):
    _, db, daemon, tmux = setup
    req = db.create_request(RequestCreate(sender_id="claude", recipient_id="gemini", prompt="Analizza"))
    db.update_request_decision(req.id, RequestStatus.APPROVED, actor="human:p")

    assert daemon.run_loop_step() == 1
    assert tmux.send_keys.call_args.args[0] == "agent-gemini"
    assert db.get_request(req.id).status == RequestStatus.DELIVERING

    db.record_reply(req.id, "gemini", "Fatto")
    assert db.decide_return(req.id, True, None, "human:p")
    _age(db, req.id)
    assert daemon.run_loop_step() == 1
    assert tmux.send_keys.call_args.args[0] == "agent-claude"
    assert "Risposta di gemini: Fatto" in tmux.send_keys.call_args.args[1]

    actors = [(e.actor, e.event) for e in db.list_events(req.id)]
    assert ("bridge", "injection_claimed") in actors
    assert ("bridge", "delivered") in actors


def test_failed_delivery_is_released_over_rest(setup):
    _, db, daemon, tmux = setup
    req = db.create_request(RequestCreate(sender_id="claude", recipient_id="human", prompt="x"))
    db.update_request_decision(req.id, RequestStatus.APPROVED, actor="human:p")
    _age(db, req.id)
    tmux.send_keys.return_value = False
    assert daemon.run_loop_step() == 0
    assert db.get_request(req.id).delivered_at is None


def test_already_delivered_is_skipped(setup):
    _, db, daemon, tmux = setup
    req = db.create_request(RequestCreate(sender_id="claude", recipient_id="human", prompt="x"))
    db.update_request_decision(req.id, RequestStatus.APPROVED, actor="human:p")
    _age(db, req.id)
    results = daemon.store.work().results
    db.mark_delivered(req.id, actor="agent:claude", via="api")  # long-poll won the race
    assert daemon.process_pending_results(results=results) == 0
    tmux.send_keys.assert_not_called()


@pytest.mark.asyncio
async def test_bridge_token_is_limited_to_bridge_endpoints(setup):
    app, db, _, _ = setup
    req = db.create_request(RequestCreate(sender_id="claude", recipient_id="human", prompt="x"))
    async with agent_client(app, BRIDGE_TOKEN) as bridge:
        assert (await bridge.get("/api/bridge/work")).status_code == 200
        decision = await bridge.post(f"/api/requests/{req.id}/decision", json={"status": "APPROVED"})
        assert decision.status_code == 403
        create = await bridge.post("/api/requests", json={"recipient_id": "human", "prompt": "x"})
        assert create.status_code == 403
        assert (await bridge.get("/api/requests")).status_code == 403
        assert (await bridge.get(f"/api/requests/{req.id}")).status_code == 404
    assert db.get_request(req.id).status == RequestStatus.PENDING


@pytest.mark.asyncio
async def test_bridge_endpoints_reject_agents_and_human(setup):
    app, _, _, _ = setup
    async with agent_client(app, "tok-claude") as claude:
        assert (await claude.get("/api/bridge/work")).status_code == 403
    async with human_client(app) as human:
        assert (await human.get("/api/bridge/work")).status_code == 403


@pytest.mark.asyncio
async def test_injected_requires_approved(setup):
    app, db, _, _ = setup
    req = db.create_request(RequestCreate(sender_id="claude", recipient_id="gemini", prompt="x"))
    async with agent_client(app, BRIDGE_TOKEN) as bridge:
        resp = await bridge.post(f"/api/bridge/requests/{req.id}/injected")
        assert resp.status_code == 409


def test_bridge_disabled_without_token():
    db = Database(os.path.join(tempfile.mkdtemp(), "placet.db"))
    db.init_db()
    app = create_app(db=db, config=CONFIG, bridge_token="")
    client = TestClient(app)
    resp = client.get("/api/bridge/work", headers={"Authorization": "Bearer "})
    assert resp.status_code == 401
    with pytest.raises(SystemExit):
        HttpStore(token="", client=client)
