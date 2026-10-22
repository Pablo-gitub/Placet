"""The cause of an unstarted Claude Desktop wake-up, reported by the bridge and shown, never acted upon."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from backend.app.models import RequestStatus
from bridge.bridge_daemon import BridgeDaemon
from bridge.claude_desktop_log import WakeDiagnosis
from tests.helpers import agent_client, human_client
from tests.test_desktop_wake_budget import SID, desktop_session, env, report, work_ids, write  # noqa: F401
from tests.test_desktop_wake_states import age


async def report_cause(app, cause="warm_capacity_denied", attempt_at=None, observed_at=None, sid=SID):
    now = datetime.now(timezone.utc)
    body = {"cause": cause, "attempt_at": (attempt_at or now).isoformat(),
            "observed_at": (observed_at or now).isoformat() if cause else None}
    async with agent_client(app, "tok-bridge") as bridge:
        return await bridge.post(f"/api/bridge/sessions/{sid}/wake-cause", json=body)


# --- the backend --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_cause_is_stored_with_the_wake_and_exposed(env):  # noqa: F811
    app, db, path = env
    await desktop_session(app)
    await write(app)
    await report(app, "open_requested")
    assert (await report_cause(app)).status_code == 200
    session = db.get_session(SID)
    assert session.wake_cause == "warm_capacity_denied" and session.wake_cause_at is not None
    async with human_client(app) as human:
        listed = {s["id"]: s for s in (await human.get("/api/sessions")).json()}
    assert listed[SID]["wake_cause"] == "warm_capacity_denied"


@pytest.mark.asyncio
async def test_only_a_closed_code_from_the_bridge_for_the_current_attempt(env):  # noqa: F811
    app, db, path = env
    await desktop_session(app)
    await write(app)
    await report(app, "open_requested")
    async with agent_client(app, "tok-bridge") as bridge:
        bad = await bridge.post(f"/api/bridge/sessions/{SID}/wake-cause",
                                json={"cause": "ram exhausted", "attempt_at": datetime.now(timezone.utc).isoformat()})
        assert bad.status_code == 422
    async with agent_client(app, "tok-claude") as claude:
        assert (await claude.post(f"/api/bridge/sessions/{SID}/wake-cause", json={"cause": None, "attempt_at": "2026-10-22T10:00:00Z"})).status_code == 403
    old = datetime.now(timezone.utc) - timedelta(hours=1)                 # an attempt that is not the current one
    ignored = await report_cause(app, attempt_at=old)
    assert ignored.status_code == 200 and db.get_session(SID).wake_cause is None
    assert (await report_cause(app, sid="nobody")).status_code == 404


@pytest.mark.asyncio
async def test_the_cause_goes_away_with_a_new_attempt_the_evidence_or_a_retry(env):  # noqa: F811
    app, db, path = env
    await desktop_session(app)
    await write(app)
    await report(app, "open_requested")
    await report_cause(app)
    await desktop_session(app)                                            # SessionStart: it started after all
    assert db.get_session(SID).wake_cause is None
    # a later episode
    db.touch_waiter(SID)
    with db._get_connection() as conn:
        conn.execute("UPDATE sessions SET waiter_seen_at = NULL WHERE id = ?", (SID,))
    await write(app, "ancora")
    await report(app, "open_requested")
    await report_cause(app)
    assert db.get_session(SID).wake_cause == "warm_capacity_denied"
    await report_cause(app, cause=None)                                   # superseded by a success in the log
    assert db.get_session(SID).wake_cause is None
    await report_cause(app)
    db.request_wake_retry(SID)
    assert db.get_session(SID).wake_cause is None


@pytest.mark.asyncio
async def test_the_cause_changes_neither_the_wake_nor_the_message_nor_triggers_anything(env):  # noqa: F811
    app, db, path = env
    await desktop_session(app)
    req_id = await write(app)
    await report(app, "open_requested")
    age(db, 120)
    before = db.get_session(SID)
    await report_cause(app)
    after = db.get_session(SID)
    assert (after.wake_state, after.wake_attempts, after.wake_at) == (before.wake_state, before.wake_attempts, before.wake_at)
    assert await work_ids(app) == []                                      # no retry, no eviction
    request = db.get_request(req_id)
    assert request.status == RequestStatus.APPROVED and request.delivery == "waiting"
    assert [e.event for e in db.list_events(req_id)] == ["created", "decided"]


# --- the bridge ---------------------------------------------------------------------------


def daemon(waker):
    store = MagicMock()
    bridge = BridgeDaemon(store=store, tmux=MagicMock(), channels={}, claude_waker=waker)
    store.reset_mock()
    return bridge, store


def test_the_bridge_diagnoses_after_an_accepted_open_and_reports_the_code_once():
    waker = MagicMock()
    waker.open.return_value = True
    bridge, store = daemon(waker)
    bridge.wake_claude_sessions([{"id": SID, "cwd": "/lab"}])
    waker.diagnose.return_value = None
    bridge.diagnose_wakes()
    store.report_wake_cause.assert_not_called()
    seen = datetime.now(timezone.utc)
    waker.diagnose.return_value = WakeDiagnosis("warm_capacity_denied", seen)
    bridge.diagnose_wakes()
    bridge.diagnose_wakes()
    store.report_wake_cause.assert_called_once()
    sid, cause, observed, attempt = store.report_wake_cause.call_args.args
    assert (sid, cause, observed) == (SID, "warm_capacity_denied", seen)
    assert abs((datetime.now(timezone.utc) - attempt).total_seconds()) < 5
    waker.diagnose.return_value = None                                    # a success appeared later in the log
    bridge.diagnose_wakes()
    assert store.report_wake_cause.call_args.args[1] is None


def test_the_bridge_asks_only_after_an_accepted_open_and_never_breaks():
    waker = MagicMock()
    waker.open.return_value = False                                       # nothing was requested
    bridge, store = daemon(waker)
    bridge.wake_claude_sessions([{"id": SID, "cwd": "/lab"}])
    bridge.diagnose_wakes()
    waker.diagnose.assert_not_called()
    waker.open.return_value = True
    bridge._claude_wake_at.clear()
    bridge.wake_claude_sessions([{"id": SID, "cwd": "/lab"}])
    waker.diagnose.side_effect = RuntimeError("log exploded")
    bridge.diagnose_wakes()                                               # swallowed
    waker.diagnose.side_effect = None
    waker.diagnose.return_value = object()                               # not a diagnosis: ignored
    bridge.diagnose_wakes()
    store.report_wake_cause.assert_not_called()
    store.report_wake_cause.side_effect = RuntimeError("backend down")
    waker.diagnose.return_value = WakeDiagnosis("warm_capacity_denied", datetime.now(timezone.utc))
    bridge.diagnose_wakes()                                               # a failed report does not stop the loop


def test_the_attempt_is_forgotten_after_a_while():
    waker = MagicMock()
    waker.open.return_value = True
    bridge, store = daemon(waker)
    bridge.wake_claude_sessions([{"id": SID, "cwd": "/lab"}])
    for info in bridge._wake_diagnoses.values():
        info["attempt_epoch"] -= 3600
    bridge.diagnose_wakes()
    waker.diagnose.assert_not_called()
    assert bridge._wake_diagnoses == {}


def test_the_attempt_time_is_taken_before_the_launcher_runs():
    """The app logs the warm-up while the link is being handed over, before the launcher returns."""
    import time as clock
    waker = MagicMock()
    seen = {}

    def slow_open(*_args, **_kwargs):
        seen["during"] = clock.time()
        clock.sleep(1.2)
        return True

    waker.open.side_effect = slow_open
    bridge, _ = daemon(waker)
    bridge.wake_claude_sessions([{"id": SID, "cwd": "/lab"}])
    assert bridge._wake_diagnoses[SID]["attempt_epoch"] <= seen["during"]
