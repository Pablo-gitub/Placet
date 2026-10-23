"""RS-09 step 2 (docs/plans/session-binding.md): a session's credential is registered by its hook as a
hash only; a presented credential binds reply/ack/progress to that session and never downgrades."""
import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.config import AgentSpec, PlacetConfig
from backend.app.database import Database
from backend.app.main import create_app
from backend.app.models import RequestCreate, RequestStatus
from tests.helpers import agent_client, human_client

CONFIG = PlacetConfig(agents={
    "claude": AgentSpec(tmux_session="agent-claude", token="tok-claude", allowed_targets=["human", "codex"]),
    "codex": AgentSpec(tmux_session="agent-codex", token="tok-codex", allowed_targets=["human", "claude"]),
})
A, B = "plst_" + "a" * 22, "plst_" + "b" * 22


def sha(cred):
    return hashlib.sha256(cred.encode()).hexdigest()


@pytest.fixture
def setup(tmp_path):
    db = Database(str(tmp_path / "placet.db"))
    db.init_db()
    return create_app(db=db, config=CONFIG), db


async def start(client, session, cred=None, provider="claude-code"):
    body = {"event": "start", "provider": provider}
    if cred:
        body["credential_sha256"] = sha(cred)
    resp = await client.post(f"/api/sessions/{session}/events", json=body)
    assert resp.status_code == 200, resp.text


async def running(app, db, session="c1"):
    """A request from Codex to Claude session `session`, being executed."""
    req = db.create_request(RequestCreate(sender_id="codex", recipient_id="claude", prompt="fai",
                                          recipient_session_id=session))
    async with human_client(app) as human:
        await human.post(f"/api/requests/{req.id}/decision", json={"status": "APPROVED"})
    async with agent_client(app, "tok-claude") as claude:
        await claude.get(f"/api/sessions/{session}/inbox")
        await claude.post(f"/api/sessions/{session}/events", json={"event": "prompt", "provider": "claude-code",
                                                                   "request_id": req.id})
    assert db.get_request(req.id).status == RequestStatus.EXECUTING
    return req


def with_cred(cred):
    return {"X-Placet-Session": cred}


@pytest.mark.asyncio
async def test_the_hook_registers_only_a_hash_and_each_start_is_a_new_generation(setup):
    app, db = setup
    async with agent_client(app, "tok-claude") as claude:
        await start(claude, "c1", A)
        await start(claude, "c1", B)
    rows = db.session_bindings("c1")
    assert [(r["generation"], r["state"]) for r in rows] == [(1, "superseded"), (2, "active")]
    assert all(len(r["sha256"]) == 64 and "plst_" not in r["sha256"] for r in rows)
    async with human_client(app) as human:
        listed = {s["id"]: s for s in (await human.get("/api/sessions")).json()}
    assert "sha256" not in str(listed["c1"]) and sha(B) not in str(listed["c1"])


@pytest.mark.asyncio
async def test_a_reply_with_the_recipients_credential_is_accepted(setup):
    app, db = setup
    async with agent_client(app, "tok-claude") as claude:
        await start(claude, "c1", A)
        req = await running(app, db)
        resp = await claude.post(f"/api/requests/{req.id}/reply", json={"content": "fatto"}, headers=with_cred(A))
    assert resp.status_code == 200, resp.text
    events = {e.event: e.details for e in db.list_events(req.id)}
    assert events["replied"].get("assurance") == "credential"


@pytest.mark.asyncio
async def test_another_sessions_credential_cannot_reply_ack_or_report_progress(setup):
    app, db = setup
    async with agent_client(app, "tok-claude") as claude:
        await start(claude, "c1", A)
        await start(claude, "c2", B)
        req = await running(app, db, "c1")
        for path, body in (("reply", {"content": "x"}), ("ack", None), ("progress", {"content": "x"})):
            resp = await claude.post(f"/api/requests/{req.id}/{path}", json=body, headers=with_cred(B))
            assert resp.status_code == 403, (path, resp.text)
            assert resp.json()["code"] == "session.not_recipient"
    assert db.get_request(req.id).response is None


@pytest.mark.asyncio
async def test_an_invalid_credential_never_downgrades_to_declared(setup):
    app, db = setup
    async with agent_client(app, "tok-claude") as claude:
        await start(claude, "c1", A)
        req = await running(app, db)
        for bad in ("plst_" + "z" * 22, "not-a-credential", ""):
            resp = await claude.post(f"/api/requests/{req.id}/reply", json={"content": "x"}, headers=with_cred(bad))
            assert resp.status_code == 401, (bad, resp.text)
            assert resp.json()["code"] == "session.credential_invalid"
    assert db.get_request(req.id).response is None


@pytest.mark.asyncio
async def test_a_credential_of_another_agent_is_refused(setup):
    app, db = setup
    async with agent_client(app, "tok-codex") as codex:
        await start(codex, "x1", B, provider="codex")
    async with agent_client(app, "tok-claude") as claude:
        await start(claude, "c1", A)
        req = await running(app, db)
        resp = await claude.post(f"/api/requests/{req.id}/reply", json={"content": "x"}, headers=with_cred(B))
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_the_previous_generation_works_only_within_the_grace_period(setup):
    app, db = setup
    async with agent_client(app, "tok-claude") as claude:
        await start(claude, "c1", A)
        req = await running(app, db)
        await start(claude, "c1", B)                                           # e.g. a compaction mid-request
        assert (await claude.post(f"/api/requests/{req.id}/progress", json={"content": "a metà"},
                                  headers=with_cred(A))).status_code == 200
        db.age_bindings("c1", timedelta(minutes=db.BINDING_GRACE_MINUTES + 1))
        assert (await claude.post(f"/api/requests/{req.id}/progress", json={"content": "dopo"},
                                  headers=with_cred(A))).status_code == 401
        assert (await claude.post(f"/api/requests/{req.id}/progress", json={"content": "dopo"},
                                  headers=with_cred(B))).status_code == 200


@pytest.mark.asyncio
async def test_without_a_credential_today_s_behaviour_is_unchanged(setup):
    app, db = setup
    async with agent_client(app, "tok-claude") as claude:
        await start(claude, "c1")
        req = await running(app, db)
        resp = await claude.post(f"/api/requests/{req.id}/reply", json={"content": "fatto"})
    assert resp.status_code == 200
    events = {e.event: e.details for e in db.list_events(req.id)}
    assert events["replied"].get("assurance", "declared") == "declared"


@pytest.mark.asyncio
async def test_a_credential_quoted_in_a_reply_or_progress_is_redacted(setup):
    app, db = setup
    async with agent_client(app, "tok-claude") as claude:
        await start(claude, "c1", A)
        req = await running(app, db)
        await claude.post(f"/api/requests/{req.id}/progress", json={"content": f"il mio token è {A}"})
        await claude.post(f"/api/requests/{req.id}/reply", json={"content": f"eccolo: {A} fine"})
    after = db.get_request(req.id)
    assert A not in (after.progress_text or "") and A not in after.response.content
    assert "[redacted]" in after.response.content


def test_the_transcript_reader_redacts_an_echoed_credential():
    from bridge.transcripts import parse_claude
    obj = {"type": "assistant", "timestamp": "2026-10-23T01:00:00Z",
           "message": {"role": "assistant", "content": [{"type": "text", "text": f"il token è {A}"},
                                                        {"type": "tool_use", "name": "Bash", "input": {"command": f"echo {A}"}}]}}
    rendered = str(parse_claude(obj))
    assert A not in rendered and "[redacted]" in rendered
