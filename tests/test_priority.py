"""RF-07: each request has a priority that drives the notification."""
import os
import tempfile

import pytest

from backend.app.config import AgentSpec, PlacetConfig
from backend.app.database import Database
from backend.app.main import create_app
from backend.app.models import Priority
from backend.app.notifier import NtfyConfig, build_notification
from tests.helpers import agent_client

CONFIG = PlacetConfig(agents={
    "claude": AgentSpec(tmux_session="agent-claude", token="tok-claude", allowed_targets=["gemini", "human"]),
    "gemini": AgentSpec(tmux_session="agent-gemini", token="tok-gemini", allowed_targets=["claude"]),
})
NTFY = NtfyConfig(server_url="https://ntfy.tail.ts.net", topic="t", base_url="https://p.tail.ts.net")


@pytest.fixture
def app():
    db = Database(os.path.join(tempfile.mkdtemp(), "placet.db"))
    db.init_db()
    return create_app(db=db, config=CONFIG)


@pytest.mark.asyncio
@pytest.mark.parametrize("body, expected", [
    ({"recipient_id": "human"}, "high"),
    ({"recipient_id": "gemini"}, "normal"),
    ({"recipient_id": "human", "priority": "low"}, "low"),
    ({"recipient_id": "gemini", "priority": "urgent"}, "urgent"),
])
async def test_priority_default_and_explicit(app, body, expected):
    async with agent_client(app, "tok-claude") as claude:
        resp = await claude.post("/api/requests", json={"prompt": "x", **body})
    assert resp.status_code == 201
    assert resp.json()["priority"] == expected


@pytest.mark.asyncio
async def test_invalid_priority_rejected(app):
    async with agent_client(app, "tok-claude") as claude:
        resp = await claude.post("/api/requests", json={"recipient_id": "human", "prompt": "x", "priority": "max"})
    assert resp.status_code == 422


@pytest.mark.parametrize("priority, ntfy", [
    (Priority.LOW, "low"), (Priority.NORMAL, "default"), (Priority.HIGH, "high"), (Priority.URGENT, "urgent"),
])
def test_ntfy_priority_mapping(priority, ntfy):
    from datetime import datetime, timezone
    from backend.app.models import QuestionType, RequestModel
    req = RequestModel(id=1, sender_id="claude", recipient_id="gemini", question_type=QuestionType.PROMPT_REVIEW,
                       prompt="x", priority=priority, created_at=datetime.now(timezone.utc))
    _, headers, _ = build_notification(req, NTFY)
    assert headers["Priority"] == ntfy
