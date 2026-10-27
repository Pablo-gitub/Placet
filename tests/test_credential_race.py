"""RS-09 timing: generations are ordered by when the hook issued them, a call right after SessionStart is retried
with backoff, and a closed session never revives a credential issued before it closed."""
import hashlib
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from backend.app.config import AgentSpec, PlacetConfig
from backend.app.database import Database
from backend.app.main import create_app
from hooks import claude_code
from mcp_server import client_api
from mcp_server.client_api import PlacetClient
from tests.helpers import agent_client

CONFIG = PlacetConfig(agents={"claude": AgentSpec(tmux_session="agent-claude", token="tok-claude", allowed_targets=["human"])})
A, B, C = ("plst_" + c * 22 for c in "abc")


def sha(cred):
    return hashlib.sha256(cred.encode()).hexdigest()


@pytest.fixture
def setup(tmp_path):
    db = Database(str(tmp_path / "placet.db"))
    db.init_db()
    db.upsert_session("s1", "claude", provider="claude-code")
    return create_app(db=db, config=CONFIG), db


def states(db, session="s1"):
    return {sha_: state for sha_, state in ((r["sha256"], r["state"]) for r in db.session_bindings(session))}


# --- the backend orders by emission ------------------------------------------------------------


def test_two_starts_that_arrive_inverted_keep_the_newest_one_active(setup):
    app, db = setup
    now = time.time()
    assert db.register_binding("s1", "claude", sha(B), issued_at=now) is not None            # B was issued later but arrives first
    assert db.register_binding("s1", "claude", sha(A), issued_at=now - 2) is not None        # A, older, arrives after
    assert states(db) == {sha(B): "active", sha(A): "superseded"}
    assert db.resolve_binding(sha(B)) is not None
    assert db.resolve_binding(sha(A)) is not None                                            # still inside its grace period


def test_an_old_start_that_arrives_late_gets_the_grace_that_started_when_it_was_replaced(setup):
    app, db = setup
    now = time.time()
    db.register_binding("s1", "claude", sha(B), issued_at=now - 20 * 60)
    db.register_binding("s1", "claude", sha(A), issued_at=now - 21 * 60)                     # replaced 20 minutes ago
    assert states(db)[sha(A)] == "superseded" and db.resolve_binding(sha(A)) is None
    assert db.resolve_binding(sha(B)) is not None


def test_in_order_starts_still_supersede_the_previous_one(setup):
    app, db = setup
    now = time.time()
    db.register_binding("s1", "claude", sha(A), issued_at=now - 5)
    db.register_binding("s1", "claude", sha(B), issued_at=now)
    assert states(db) == {sha(A): "superseded", sha(B): "active"}


def test_a_start_without_a_time_is_ordered_by_arrival_as_before(setup):
    app, db = setup
    db.register_binding("s1", "claude", sha(A))
    db.register_binding("s1", "claude", sha(B))
    assert states(db) == {sha(A): "superseded", sha(B): "active"}


def test_a_time_in_the_future_cannot_shadow_later_credentials(setup):
    app, db = setup
    db.register_binding("s1", "claude", sha(A), issued_at=time.time() + 10 * 3600)           # bogus clock
    db.register_binding("s1", "claude", sha(B), issued_at=time.time())
    assert states(db)[sha(B)] == "active"


@pytest.mark.asyncio
async def test_the_event_carries_the_time_and_inverted_events_are_ordered(setup):
    app, db = setup
    now = time.time()
    async with agent_client(app, "tok-claude") as claude:
        for cred, issued in ((B, now), (A, now - 3)):
            resp = await claude.post("/api/sessions/s1/events", json={
                "event": "start", "provider": "claude-code", "credential_sha256": sha(cred), "credential_issued_at": issued})
            assert resp.status_code == 200, resp.text
    assert states(db) == {sha(B): "active", sha(A): "superseded"}


def test_the_hook_stamps_the_credential_with_the_time_it_was_issued(monkeypatch):
    monkeypatch.setenv("PLACET_SESSION_CREDENTIALS", "1")
    payload = claude_code.build_event({"hook_event_name": "SessionStart", "source": "startup", "cwd": "/p"})
    before = time.time()
    claude_code.issue_credential(payload)
    assert before <= payload["credential_issued_at"] <= time.time()


# --- a closed session stays closed -------------------------------------------------------------


def test_a_session_that_closes_and_reopens_does_not_revive_the_old_credential(setup):
    app, db = setup
    db.register_binding("s1", "claude", sha(A), issued_at=time.time())
    assert db.resolve_binding(sha(A)) is not None
    db.end_session("s1")
    assert db.resolve_binding(sha(A)) is None
    db.reopen_session("s1")                                                                  # ended_at is NULL again
    assert db.get_session("s1").ended_at is None
    assert db.resolve_binding(sha(A)) is None                                                # but the old credential is not valid
    db.register_binding("s1", "claude", sha(B), issued_at=time.time())                       # a new start issues a new one
    assert db.resolve_binding(sha(B)) is not None and db.resolve_binding(sha(A)) is None


def test_a_continued_session_stays_dead_too(setup):
    app, db = setup
    db.upsert_session("s2", "claude", provider="claude-code")
    db.register_binding("s1", "claude", sha(A), issued_at=time.time())
    db.continue_session("s1", "s2")
    assert db.resolve_binding(sha(A)) is None


# --- the MCP client waits for the hash to arrive --------------------------------------------------


def answer(code):
    body = {"code": "session.credential_invalid"} if code == 401 else {"status": "EXECUTING"}
    return httpx.Response(code, json=body, request=httpx.Request("POST", "http://x/api"))


@pytest.fixture
def slept(monkeypatch):
    waits = []
    monkeypatch.setattr(client_api.time, "sleep", waits.append)
    return waits


def test_a_call_right_after_the_start_succeeds_after_backing_off(monkeypatch, slept):
    sent, answers = [], [401, 401, 200]

    def fake_post(self, url, json=None, headers=None, **kw):
        sent.append(headers or {})
        return answer(answers.pop(0))

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    PlacetClient(base_url="http://x", api_token="t").ack(1, session_token=A)
    assert len(sent) == 3 and all(h.get("X-Placet-Session") == A for h in sent)
    assert slept == [0.5, 1.0]


def test_a_wrong_credential_is_still_refused_after_three_retries_never_sent_without_it(monkeypatch, slept):
    sent = []

    def fake_post(self, url, json=None, headers=None, **kw):
        sent.append(headers or {})
        return answer(401)

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    with pytest.raises(httpx.HTTPStatusError) as refused:
        PlacetClient(base_url="http://x", api_token="t").ack(1, session_token=A)
    assert refused.value.response.status_code == 401
    assert len(sent) == 4 and all(h.get("X-Placet-Session") == A for h in sent)             # first try + 3 retries, always with it
    assert slept == [0.5, 1.0, 2.0]


def test_the_coordinator_view_read_is_retried_the_same_way(monkeypatch, slept):
    answers = [401, 200]

    def fake_get(self, url, headers=None, params=None, **kw):
        code = answers.pop(0)
        body = {"code": "session.credential_invalid"} if code == 401 else {"project": {"id": 1}, "requests": []}
        return httpx.Response(code, json=body, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.Client, "get", fake_get)
    assert PlacetClient(base_url="http://x", api_token="t").project_view(1, A)["project"]["id"] == 1
    assert slept == [0.5]


def test_nothing_is_retried_without_a_credential_or_for_another_refusal(monkeypatch, slept):
    calls = []

    def fake_post(self, url, json=None, headers=None, **kw):
        calls.append(headers or {})
        return httpx.Response(401, json={"code": "auth.unauthorized"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    with pytest.raises(httpx.HTTPStatusError):
        PlacetClient(base_url="http://x", api_token="t").ack(1, session_token=A)             # 401, but not the credential's
    with pytest.raises(httpx.HTTPStatusError):
        PlacetClient(base_url="http://x", api_token="t").ack(1)                              # no credential at all
    assert len(calls) == 2 and slept == []
