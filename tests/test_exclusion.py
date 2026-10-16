"""`.placet-off` excludes a project everywhere (RF-45): hidden, not data deleted."""
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from backend.app.models import RequestStatus
from bridge.bridge_daemon import BridgeDaemon
from bridge.stores import HttpStore
from tests.helpers import agent_client, human_client
from tests.test_session_messages import setup, write  # noqa: F401


async def start_in(app, agent, session_id, provider, cwd):
    async with agent_client(app, f"tok-{agent}") as client:
        await client.post(f"/api/sessions/{session_id}/events",
                          json={"event": "start", "provider": provider, "cwd": cwd})


def daemon_for(app, codex=None):
    return BridgeDaemon(store=HttpStore(token="tok-bridge", client=TestClient(app)),
                        tmux=MagicMock(), channels={"codex": codex or MagicMock()})


@pytest.mark.asyncio
async def test_an_excluded_project_disappears_and_gets_nothing(setup, tmp_path):  # noqa: F811
    app, db = setup
    secret, other = tmp_path / "secret", tmp_path / "other"
    (secret / "sub").mkdir(parents=True)
    other.mkdir()
    await start_in(app, "codex", "cx1", "codex", str(secret / "sub"))
    await start_in(app, "codex", "cx2", "codex", str(other))
    waiting = (await write(app, "cx1", "prima dell'esclusione")).json()["id"]
    async with agent_client(app, "tok-claude") as claude:
        question = (await claude.post("/api/requests", json={
            "recipient_id": "human", "question_type": "confirmation", "prompt": "ok?"})).json()["id"]
    db.link_sender_session(question, "cx1", "claude")  # as if asked from that session

    (secret / ".placet-off").touch()
    codex = MagicMock()
    daemon = daemon_for(app, codex)
    daemon.run_loop_step()

    session = db.get_session("cx1")
    assert session.excluded and not session.reachable
    req = db.get_request(waiting)
    assert (req.status, req.delivery) == (RequestStatus.CANCELLED, "cancelled")
    assert {"reason": "progetto escluso"} in [e.details for e in db.list_events(waiting)]
    codex.send.assert_not_called()

    async with human_client(app) as human:
        sessions = (await human.get("/api/sessions?include_ended=true")).json()
        assert [s["id"] for s in sessions] == ["cx2"]
        listed = [r["id"] for r in (await human.get("/api/requests")).json()]
        assert waiting not in listed and question not in listed
        assert (await human.get("/api/sessions/cx1/transcript")).status_code == 409
        resp = await human.post("/api/sessions/cx1/messages", json={"text": "x"})
        assert resp.status_code == 409 and ".placet-off" in resp.json()["detail"]
    # Still in the database, for the audit.
    assert db.get_request(question) is not None

    # Removing the file brings the project back.
    (secret / ".placet-off").unlink()
    daemon._folders_at -= 60
    daemon.run_loop_step()
    assert not db.get_session("cx1").excluded
    async with human_client(app) as human:
        assert {s["id"] for s in (await human.get("/api/sessions")).json()} == {"cx1", "cx2"}


def test_the_bridge_refuses_to_read_an_excluded_conversation(tmp_path):
    (tmp_path / ".placet-off").touch()
    result = BridgeDaemon(store=MagicMock(), tmux=MagicMock(), channels={}).handle_job(
        {"id": "j1", "kind": "transcript", "provider": "codex", "session_id": "x", "cwd": str(tmp_path)})
    assert result["ok"] is False and result["error"] == "excluded"


@pytest.mark.asyncio
async def test_folders_are_checked_now_and_then(setup, tmp_path):  # noqa: F811
    app, db = setup
    await start_in(app, "codex", "cx1", "codex", str(tmp_path))
    store = MagicMock()
    daemon = BridgeDaemon(store=store, tmux=MagicMock(), channels={})
    daemon.check_folders([str(tmp_path)])
    daemon.check_folders([str(tmp_path)])
    assert store.report_folders.call_count == 1
    store.report_folders.assert_called_with([str(tmp_path)], [])


@pytest.mark.asyncio
async def test_agents_cannot_target_an_excluded_session(tmp_path):
    import os
    import tempfile
    from backend.app.config import AgentSpec, PlacetConfig
    from backend.app.database import Database
    from backend.app.main import create_app
    db = Database(os.path.join(tempfile.mkdtemp(), "placet.db"))
    db.init_db()
    app = create_app(db=db, bridge_token="tok-bridge", config=PlacetConfig(agents={
        "claude": AgentSpec(tmux_session="a", token="tok-claude", allowed_targets=["human", "codex"]),
        "codex": AgentSpec(tmux_session="b", token="tok-codex", allowed_targets=["human"]),
    }))
    await start_in(app, "codex", "cx1", "codex", str(tmp_path))
    db.set_excluded_folders([str(tmp_path)], [str(tmp_path)])
    async with agent_client(app, "tok-claude") as claude:
        resp = await claude.post("/api/requests", json={
            "recipient_id": "codex", "question_type": "prompt_review", "prompt": "x",
            "recipient_session_id": "cx1"})
    assert resp.status_code == 422 and "excluded" in resp.json()["detail"]
