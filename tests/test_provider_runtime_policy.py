"""The active provider policy also applies to pre-upgrade installations."""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from backend.app.database import Database
from backend.app.main import create_app
from backend.app.models import RequestCreate
from bridge.bridge_daemon import BridgeDaemon
from bridge.channels import default_channels
from bridge.stores import Work
from tests.helpers import agent_client, human_client


@pytest.fixture
def active_app(tmp_path, monkeypatch):
    monkeypatch.setenv("PLACET_TOKEN_CLAUDE", "test-claude")
    monkeypatch.setenv("PLACET_TOKEN_GEMINI", "test-gemini")
    monkeypatch.delenv("PLACET_AGENTS_FILE", raising=False)
    monkeypatch.delenv("PLACET_EXPERIMENTAL_CLAUDE_DESKTOP", raising=False)
    db = Database(str(tmp_path / "placet.db"))
    db.init_db()
    db.upsert_session("old-google", "gemini", provider="antigravity")
    db.upsert_session("old-alias", "claude", provider="antigravity")
    db.upsert_session("old-claude", "claude", provider="claude-code", entrypoint="claude-desktop")
    db.create_node("old-google-node", "old-google")
    db.set_setting("claude_auto_renew", "true")
    db.set_desktop_capacity(2, 4)
    return create_app(db=db, bridge_token="test-bridge"), db


@pytest.mark.asyncio
async def test_antigravity_is_paused_with_legacy_sessions(active_app):
    app, db = active_app
    queued = db.create_request(RequestCreate(
        sender_id="claude", recipient_id="gemini", recipient_session_id="old-google", prompt="already queued"
    ))
    async with human_client(app) as human:
        assert {s["id"] for s in (await human.get("/api/sessions")).json()} == {"old-claude"}
        assert (await human.get("/api/nodes")).json() == []
        assert (await human.get("/api/requests")).json() == []
        for path in ("transcript", "attachments/0:0:0"):
            assert (await human.get(f"/api/sessions/old-google/{path}")).status_code == 404
        assert (await human.post("/api/sessions/old-google/messages", json={"text": "hello"})).status_code == 404
    async with agent_client(app, "test-gemini") as gemini:
        assert (await gemini.post("/api/sessions/old-google/events", json={"event": "start"})).status_code == 401
    async with agent_client(app, "test-claude") as claude:
        assert "gemini" not in (await claude.get("/api/agents")).json()["agents"]
        assert (await claude.post("/api/sessions/old-alias/events", json={"event": "start"})).status_code == 404
        assert (await claude.post("/api/sessions/new-google/events",
                                  json={"event": "start", "provider": "antigravity"})).status_code == 404
    async with agent_client(app, "test-bridge") as bridge:
        work = (await bridge.get("/api/bridge/work")).json()
        assert work["titles"] == []
        assert "antigravity" not in default_channels()
        assert (await bridge.post("/api/bridge/sessions/old-google/take")).status_code == 404
    assert db.get_session("old-google") is not None
    assert db.get_request(queued.id).prompt == "already queued"


@pytest.mark.asyncio
async def test_retired_usage_does_not_hold_messages_or_accept_new_readings(active_app):
    app, db = active_app
    reset = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    reports = [{"provider": provider, "buckets": [
        {"id": "5h", "window": "5h", "used_percent": 100, "resets_at": reset}
    ]} for provider in ("claude-code", "antigravity", "codex")]
    for report in reports:
        db.set_usage(report["provider"], json.dumps(report))
    assert db.limited_providers() == {"codex"}
    assert db.provider_reset_time("claude-code", 90, 95) is None
    async with agent_client(app, "test-bridge") as bridge:
        assert (await bridge.post("/api/bridge/usage", json={"providers": reports})).json() == {"providers": ["codex"]}
        work = (await bridge.get("/api/bridge/work")).json()
        assert "claude_auto_renew" not in work
        assert work["claude_desktop_experimental"] is False
    async with human_client(app) as human:
        assert [item["provider"] for item in (await human.get("/api/usage")).json()] == ["codex"]
        assert (await human.put("/api/settings/claude-renew", json={"enabled": True})).status_code in (404, 405)
        assert (await human.post("/api/usage/claude/renew")).status_code in (404, 405)
        assert (await human.post("/api/sessions/old-claude/wake")).status_code == 409
        assert (await human.get("/api/claude-desktop/capacity")).json() == {}


def test_bridge_skips_experimental_desktop_and_legacy_renew_jobs(monkeypatch, tmp_path):
    store = Mock()
    store.work.return_value = Work(claude_wake_sessions=[{"id": "old-claude"}])
    daemon = BridgeDaemon(store=store, flags_dir=tmp_path)
    for name in ("diagnose_wakes", "refresh_desktop_state", "wake_claude_sessions"):
        monkeypatch.setattr(daemon, name, Mock(side_effect=AssertionError("desktop internals were used")))
    daemon.run_loop_step()
    assert daemon.handle_job({"kind": "claude_renew"})["error"] == "unknown_job"
    assert daemon.handle_job({"kind": "transcript", "provider": "antigravity"})["error"] == "not_found"
