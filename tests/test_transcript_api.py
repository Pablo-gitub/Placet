"""
Conversation pages for the PWA: the backend asks the bridge (which can read
the providers' files) and waits for its answer (RF-30, RF-31).
"""
import asyncio
import json
import os
import tempfile

import pytest

from backend.app.bridge_jobs import BridgeJobs, BridgeOffline, BridgeTimeout
from backend.app.config import AgentSpec, PlacetConfig
from backend.app.database import Database
from backend.app.main import create_app
from bridge.bridge_daemon import BridgeDaemon
from bridge.stores import HttpStore
from tests.helpers import agent_client, human_client

CONFIG = PlacetConfig(agents={
    "claude": AgentSpec(tmux_session="agent-claude", token="tok-claude", allowed_targets=["human"]),
})
SESSION = "a2c3ef2a-6d99-4567-9909-9fc96489ebf7"
PAGE = {"session_id": SESSION, "provider": "claude-code",
        "messages": [{"role": "user", "text": "ciao", "at": None, "cursor": "0:0"}], "before": None, "end": 10}


@pytest.fixture
def setup():
    db = Database(os.path.join(tempfile.mkdtemp(), "placet.db"))
    db.init_db()
    app = create_app(db=db, config=CONFIG, bridge_token="tok-bridge", transcripts_local=False)
    return app, db


async def register(app, session_id=SESSION):
    async with agent_client(app, "tok-claude") as claude:
        resp = await claude.post(f"/api/sessions/{session_id}/events",
                                 json={"event": "start", "provider": "claude-code", "cwd": "/p"})
        assert resp.status_code == 200


async def fake_bridge(app, handle, polls=1):
    """Takes jobs like the real bridge and answers with handle(job)."""
    async with agent_client(app, "tok-bridge") as bridge:
        for _ in range(polls):
            resp = await bridge.get("/api/bridge/jobs?wait_seconds=5")
            if resp.status_code == 204:
                continue
            for job in resp.json()["jobs"]:
                result = await bridge.post(f"/api/bridge/jobs/{job['id']}/result", json=handle(job))
                assert result.json() == {"accepted": True}


@pytest.mark.asyncio
async def test_page_comes_from_the_bridge(setup):
    app, _ = setup
    await register(app)
    seen = []

    def handle(job):
        seen.append(job)
        return {"ok": True, "page": PAGE}

    # The bridge is polling before the PWA asks.
    bridge = asyncio.create_task(fake_bridge(app, handle))
    await asyncio.sleep(0.05)
    async with human_client(app) as human:
        resp = await human.get(f"/api/sessions/{SESSION}/transcript?limit=20&before=100")
    await bridge
    assert resp.status_code == 200 and resp.json() == PAGE
    job = seen[0]
    assert job["kind"] == "transcript" and job["session_id"] == SESSION
    assert job["provider"] == "claude-code" and job["agent_id"] == "claude"
    assert (job["limit"], job["before"], job["after"]) == (20, 100, None)
    assert "path" not in json.dumps(job)  # the bridge locates the file itself (RS-06)


@pytest.mark.asyncio
async def test_bridge_errors_become_http_errors(setup):
    app, _ = setup
    await register(app)
    for code, status in [("not_found", 404), ("unsupported", 422), ("internal", 502)]:
        bridge = asyncio.create_task(fake_bridge(app, lambda job: {"ok": False, "error": code, "message": "x"}))
        await asyncio.sleep(0.05)
        async with human_client(app) as human:
            resp = await human.get(f"/api/sessions/{SESSION}/transcript")
        await bridge
        assert resp.status_code == status


@pytest.mark.asyncio
async def test_bridge_offline(setup):
    app, _ = setup
    await register(app)
    async with human_client(app) as human:
        resp = await human.get(f"/api/sessions/{SESSION}/transcript")
    assert resp.status_code == 503 and "bridge" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_only_the_human_reads_and_only_the_bridge_answers(setup):
    app, _ = setup
    await register(app)
    async with agent_client(app, "tok-claude") as claude:
        assert (await claude.get(f"/api/sessions/{SESSION}/transcript")).status_code == 403
        assert (await claude.get("/api/bridge/jobs")).status_code == 403
        assert (await claude.post("/api/bridge/jobs/x/result", json={"ok": True})).status_code == 403
    async with agent_client(app, "tok-bridge") as bridge:
        assert (await bridge.get(f"/api/sessions/{SESSION}/transcript")).status_code == 403
        assert (await bridge.get("/api/bridge/jobs")).status_code == 204
        assert (await bridge.post("/api/bridge/jobs/unknown/result", json={"ok": True})).json() == {"accepted": False}
    async with human_client(app) as human:
        assert (await human.get("/api/sessions/nope/transcript")).status_code == 404
        assert (await human.get(f"/api/sessions/{SESSION}/transcript?limit=0")).status_code == 422


@pytest.mark.asyncio
async def test_local_mode_reads_directly(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    path = tmp_path / ".claude" / "projects" / "-p" / f"{SESSION}.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"type": "user", "message": {"content": "ciao"}}) + "\n")
    db = Database(str(tmp_path / "placet.db"))
    db.init_db()
    app = create_app(db=db, config=CONFIG, transcripts_local=True)
    await register(app)
    async with human_client(app) as human:
        resp = await human.get(f"/api/sessions/{SESSION}/transcript")
    assert resp.status_code == 200
    assert [m["text"] for m in resp.json()["messages"]] == ["ciao"]


# --- The broker --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_jobs_wait_for_the_bridge_and_time_out():
    jobs = BridgeJobs()
    with pytest.raises(BridgeOffline):
        await jobs.run("transcript", {})
    assert await jobs.take(0) == []  # the bridge polls: now it is alive
    with pytest.raises(BridgeTimeout):
        await jobs.run("transcript", {}, timeout=0.05)
    # A timed-out job is not handed out later.
    assert await jobs.take(0) == []

    async def answer():
        taken = await jobs.take(2)
        assert jobs.resolve(taken[0]["id"], {"ok": True, "page": {"n": 1}})
        assert not jobs.resolve(taken[0]["id"], {"ok": True})  # only once

    task = asyncio.create_task(answer())
    assert await jobs.run("transcript", {"x": 1}, timeout=2) == {"ok": True, "page": {"n": 1}}
    await task


# --- The real bridge worker against the API ------------------------------------------


@pytest.mark.asyncio
async def test_bridge_daemon_answers_jobs(setup, tmp_path, monkeypatch):
    app, _ = setup
    await register(app)
    monkeypatch.setenv("HOME", str(tmp_path))
    path = tmp_path / ".claude" / "projects" / "-p" / f"{SESSION}.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"type": "user", "message": {"content": "dal bridge"}}) + "\n")
    daemon = BridgeDaemon(store=object.__new__(HttpStore), tmux=object())

    async def serve_one():
        # One iteration of the daemon's job loop, against the app.
        async with agent_client(app, "tok-bridge") as client:
            resp = await client.get("/api/bridge/jobs?wait_seconds=5")
            for job in resp.json()["jobs"]:
                result = await asyncio.to_thread(daemon.handle_job, job)
                await client.post(f"/api/bridge/jobs/{job['id']}/result", json=result)

    bridge = asyncio.create_task(serve_one())
    await asyncio.sleep(0.05)
    async with human_client(app) as human:
        resp = await human.get(f"/api/sessions/{SESSION}/transcript")
    await bridge
    assert resp.status_code == 200
    assert [m["text"] for m in resp.json()["messages"]] == ["dal bridge"]


def test_daemon_reports_errors_as_results():
    daemon = BridgeDaemon(store=object.__new__(HttpStore), tmux=object())
    assert daemon.handle_job({"kind": "other"})["error"] == "unknown_job"
    assert daemon.handle_job({"kind": "transcript", "provider": "claude-code",
                              "session_id": "../etc"})["error"] == "invalid"
