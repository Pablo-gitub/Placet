"""Usage windows per provider (RF-60): read by the bridge, shown by the PWA."""
import json
import os
from unittest.mock import MagicMock

import pytest

from bridge.bridge_daemon import BridgeDaemon
from bridge.usage import antigravity_usage, codex_usage, read_all
from tests.helpers import agent_client, human_client
from tests.test_session_messages import setup  # noqa: F401


def _rollout(home, name, entries, mtime):
    folder = home / ".codex" / "sessions" / "2026" / "10" / "18"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"rollout-{name}.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in entries))
    os.utime(path, (mtime, mtime))
    return path


def _limits(stamp, used5, used7, limit_id="codex"):
    return {"timestamp": stamp, "type": "event_msg", "payload": {"type": "token_count", "rate_limits": {
        "limit_id": limit_id, "plan_type": "plus",
        "primary": {"used_percent": used5, "window_minutes": 300, "resets_at": 1792350166},
        "secondary": {"used_percent": used7, "window_minutes": 10080, "resets_at": 1792917158}}}}


def test_codex_takes_the_newest_limits_with_windows(tmp_path):
    premium = {"timestamp": "2026-10-18T17:50:00Z", "type": "event_msg", "payload": {
        "type": "token_count", "rate_limits": {"limit_id": "premium", "primary": None, "secondary": None}}}
    _rollout(tmp_path, "old", [_limits("2026-10-18T10:00:00Z", 10, 5)], 1000)
    _rollout(tmp_path, "new", [_limits("2026-10-18T17:48:00Z", 100, 26), premium], 2000)
    usage = codex_usage(tmp_path)
    assert usage["plan"] == "plus" and usage["observed_at"] == "2026-10-18T17:48:00Z"
    assert [(b["window"], b["used_percent"]) for b in usage["buckets"]] == [("5h", 100.0), ("7d", 26.0)]
    assert usage["buckets"][0]["resets_at"].startswith("2026-")


def test_codex_without_rollouts_says_nothing(tmp_path):
    assert codex_usage(tmp_path) is None


def test_antigravity_remaining_fractions_become_used_percentages():
    summary = {"response": {"groups": [{"displayName": "Gemini Models", "buckets": [
        {"bucketId": "gemini-5h", "window": "5h", "remainingFraction": 0.88, "resetTime": "2026-10-18T23:04:53Z"},
        {"bucketId": "gemini-weekly", "window": "weekly", "remainingFraction": 0.25},
        {"bucketId": "odd", "window": "monthly", "remainingFraction": 0.5}]}]}}
    usage = antigravity_usage(summary)
    assert [(b["id"], b["window"], b["used_percent"]) for b in usage["buckets"]] == [
        ("gemini-5h", "5h", 12.0), ("gemini-weekly", "7d", 75.0)]
    assert usage["buckets"][0]["group"] == "Gemini Models"
    assert antigravity_usage({}) is None


def test_a_failing_reader_does_not_hide_the_others(tmp_path):
    _rollout(tmp_path, "a", [_limits("2026-10-18T17:48:00Z", 30, 10)], 1000)
    broken = MagicMock()
    broken.quota_summary.side_effect = RuntimeError("not running")
    assert [u["provider"] for u in read_all(antigravity=broken, home=tmp_path)] == ["codex"]


@pytest.mark.asyncio
async def test_bridge_reports_and_the_pwa_reads(setup):  # noqa: F811
    app, db = setup
    report = {"providers": [{"provider": "codex", "plan": "plus", "observed_at": "2026-10-18T17:48:00Z",
                             "buckets": [{"id": "5h", "window": "5h", "used_percent": 33,
                                          "resets_at": "2026-10-18T19:02:46Z"}]}]}
    async with agent_client(app, "tok-codex") as agent:
        assert (await agent.post("/api/bridge/usage", json=report)).status_code == 403
    async with agent_client(app, "tok-bridge") as bridge:
        assert (await bridge.post("/api/bridge/usage", json=report)).status_code == 200
        bad = {"providers": [{**report["providers"][0], "buckets": [
            {"id": "5h", "window": "5h", "used_percent": 130}]}]}
        assert (await bridge.post("/api/bridge/usage", json=bad)).status_code == 422
        assert (await bridge.get("/api/usage")).status_code == 403
    async with human_client(app) as human:
        data = (await human.get("/api/usage")).json()
    assert data[0]["provider"] == "codex" and data[0]["buckets"][0]["used_percent"] == 33


def test_bridge_reads_usage_once_a_minute(monkeypatch):
    store = MagicMock()
    reads = []
    monkeypatch.setattr("bridge.bridge_daemon.read_usage",
                        lambda **kw: reads.append(1) or [{"provider": "codex", "buckets": []}])
    daemon = BridgeDaemon(store=store, tmux=MagicMock(), channels={})
    daemon.refresh_usage()
    daemon.refresh_usage()
    assert len(reads) == 1 and store.report_usage.call_count == 1


@pytest.mark.asyncio
async def test_messages_wait_while_the_provider_is_at_its_limit(setup):  # noqa: F811
    from datetime import datetime, timedelta, timezone
    from tests.test_session_messages import start, write
    app, db = setup
    await start(app, "codex", "cx1", "codex")
    await write(app, "cx1", "riprendi")
    soon = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    full = {"provider": "codex", "buckets": [{"id": "5h", "window": "5h", "used_percent": 100, "resets_at": soon}]}
    db.set_usage("codex", json.dumps(full))
    async with agent_client(app, "tok-bridge") as bridge:
        assert (await bridge.get("/api/bridge/work")).json()["sessions"] == []
        # Once the window has reset, the message goes.
        later = datetime.now(timezone.utc) + timedelta(hours=1)
        assert db.limited_providers(now=later) == set()
        past = {**full, "buckets": [{**full["buckets"][0], "resets_at": "2020-01-01T00:00:00+00:00"}]}
        db.set_usage("codex", json.dumps(past))
        assert [s["id"] for s in (await bridge.get("/api/bridge/work")).json()["sessions"]] == ["cx1"]
    # Per-model limits (Antigravity) never hold messages.
    db.set_usage("antigravity", json.dumps({"provider": "antigravity", "buckets": [
        {"id": "g", "group": "Gemini", "window": "5h", "used_percent": 100, "resets_at": soon}]}))
    assert db.limited_providers() == set()
