"""Usage guard: automatic relations of a provider near its usage limit fall back to review."""
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.models import ProviderUsage
from tests.helpers import agent_client, human_client
from tests.test_relations import env, event, post, two_nodes  # noqa: F401  (fixture)


def usage(db, provider, five=None, seven=None, observed=None, group=None):
    soon = datetime.now(timezone.utc) + timedelta(hours=2)
    buckets = []
    if five is not None:
        buckets.append({"id": "5h", "group": group, "window": "5h", "used_percent": five, "resets_at": soon.isoformat()})
    if seven is not None:
        buckets.append({"id": "7d", "group": group, "window": "7d", "used_percent": seven, "resets_at": soon.isoformat()})
    row = ProviderUsage(provider=provider, observed_at=observed or datetime.now(timezone.utc), buckets=buckets)
    db.set_usage(provider, row.model_dump_json())


async def automatic_pair(app, db):
    a, b = await two_nodes(app, db)  # claude (busy sender) -> codex
    review = db.create_relation_type("review", "")
    db.create_relation(a.id, b.id, review.id, "automatic")
    db.create_relation(b.id, a.id, review.id, "automatic")
    return a, b


async def guard(app, **body):
    async with human_client(app) as human:
        resp = await human.put("/api/settings/usage-guard", json=body)
        assert resp.status_code == 200, resp.text
        return resp.json()


@pytest.mark.asyncio
async def test_off_by_default_and_settable(env):
    app, db = env
    async with human_client(app) as human:
        out = (await human.get("/api/settings/usage-guard")).json()
    assert out == {"enabled": False, "five_hour": 90, "seven_day": 95, "guarded": []}
    out = await guard(app, enabled=True, five_hour=80, seven_day=97)
    assert (out["enabled"], out["five_hour"], out["seven_day"]) == (True, 80, 97)
    async with human_client(app) as human:
        assert (await human.put("/api/settings/usage-guard", json={"enabled": True, "five_hour": 120, "seven_day": 95})).status_code == 422
    async with agent_client(app, "tok-claude") as client:
        assert (await client.get("/api/settings/usage-guard")).status_code == 403
        assert (await client.put("/api/settings/usage-guard", json={"enabled": False, "five_hour": 90, "seven_day": 95})).status_code == 403


@pytest.mark.asyncio
async def test_a_recipient_near_its_5h_limit_gets_reviewed_prompts(env):
    app, db = env
    await automatic_pair(app, db)
    usage(db, "codex", five=91, seven=10)
    # Disabled: still automatic.
    out = (await post(app, {"target_node": "builder"})).json()
    assert out["delivery_policy"] == "automatic"
    await guard(app, enabled=True, five_hour=90, seven_day=95)
    async with human_client(app) as human:
        assert (await human.get("/api/settings/usage-guard")).json()["guarded"] == ["codex"]
    resp = await post(app, {"target_node": "builder"})
    out = resp.json()
    assert resp.status_code == 201 and out["status"] == "PENDING"
    assert out["delivery_policy"] == "manual" and out["automation_limit"] == "usage"
    events = [e for e in db.list_events(out["id"]) if e.event == "automation_limited"]
    assert events and events[0].details["reason"] == "usage" and events[0].details["provider"] == "codex"


@pytest.mark.asyncio
async def test_a_sender_near_its_7d_limit_and_the_way_back(env):
    app, db = env
    await automatic_pair(app, db)
    await guard(app, enabled=True, five_hour=90, seven_day=95)
    usage(db, "claude-code", five=5, seven=96)
    out = (await post(app, {"target_node": "builder"})).json()
    assert out["delivery_policy"] == "manual" and out["automation_limit"] == "usage"
    # Below the thresholds again: automatic, with the reply coming back on its own.
    usage(db, "claude-code", five=5, seven=50)
    out = (await post(app, {"target_node": "builder"})).json()
    assert out["delivery_policy"] == "automatic" and out["allow_auto_return"] is True


@pytest.mark.asyncio
async def test_no_automatic_reply_towards_a_guarded_sender(env):
    app, db = env
    await automatic_pair(app, db)
    await guard(app, enabled=True, five_hour=90, seven_day=95)
    out = (await post(app, {"target_node": "builder"})).json()
    assert out["allow_auto_return"] is True
    # The reply would land in the sender's session while it is near its limit.
    usage(db, "claude-code", five=95)
    out = (await post(app, {"target_node": "builder"})).json()
    assert out["delivery_policy"] == "manual" and out["allow_auto_return"] is False


@pytest.mark.asyncio
async def test_stale_or_expired_readings_and_model_groups(env):
    app, db = env
    await automatic_pair(app, db)
    await guard(app, enabled=True, five_hour=90, seven_day=95)
    # Claude's reading is old (token expired): unknown, not a reason to review.
    usage(db, "claude-code", five=99, observed=datetime.now(timezone.utc) - timedelta(hours=1))
    assert (await post(app, {"target_node": "builder"})).json()["delivery_policy"] == "automatic"
    # A model group of a provider counts (Placet does not know which model runs).
    usage(db, "codex", five=93, group="Gemini models")
    assert (await post(app, {"target_node": "builder"})).json()["automation_limit"] == "usage"
