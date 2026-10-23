"""RFC-17 review #379: enrollment of the session, gated providers, and the narrower projection and audit."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.models import RequestStatus
from tests.helpers import agent_client, human_client
from tests.test_coordinator_view import (CRED_NEW, DEV_CRED, OTHER_CRED, PM_CRED, env, header, make_request, sha, start,  # noqa: F401
                                        view, world)


async def confirm(app, project, node, session_id):
    async with human_client(app) as human:
        return await human.post(f"/api/projects/{project.id}/nodes/{node.id}/view-grant/confirm",
                                json={"session_id": session_id})


async def detail_node(app, project, node):
    async with human_client(app) as human:
        return next(n for n in (await human.get(f"/api/projects/{project.id}")).json()["nodes"] if n["id"] == node.id)


# --- 1. enrollment ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_granting_while_a_session_is_attached_enrolls_it_at_once(env):
    app, db = env
    p, q, pm, dev, rel = await world(app, db)
    assert (await view(app, p, PM_CRED)).status_code == 200
    node = await detail_node(app, p, pm)
    assert node["coordinator_view"] is True and node["coordinator_session"]["state"] == "confirmed"


@pytest.mark.asyncio
async def test_a_grant_made_without_a_session_waits_for_the_first_one_to_be_confirmed(env):
    app, db = env
    p, q, pm, dev, rel = await world(app, db)
    await start(app, "claude", "late-s", "plst_" + "l" * 22)
    late = db.create_node("late", "late-s")
    with db._get_connection() as conn:
        conn.execute("UPDATE sessions SET node_id = NULL WHERE id = 'late-s'")        # a node with no session yet
    db.add_node_to_project(p.id, late.id)
    assert db.grant_view(p.id, late.id, "human:test") is not None
    assert (await detail_node(app, p, late))["coordinator_session"]["state"] == "no_session"
    with db._get_connection() as conn:
        conn.execute("UPDATE sessions SET node_id = ? WHERE id = 'late-s'", (late.id,))
    assert (await view(app, p, "plst_" + "l" * 22)).status_code == 403              # first session: not yet
    assert (await detail_node(app, p, late))["coordinator_session"]["state"] == "pending_confirmation"
    assert (await confirm(app, p, late, "late-s")).status_code == 200
    assert (await view(app, p, "plst_" + "l" * 22)).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["continuation", "rebind"])
async def test_a_node_that_moves_to_another_session_needs_the_user_again(env, how):
    app, db = env
    p, q, pm, dev, rel = await world(app, db)
    await start(app, "claude", "pm-new", CRED_NEW)
    if how == "continuation":
        db.continue_session("pm-s", "pm-new")
    else:
        with db._get_connection() as conn:
            conn.execute("UPDATE sessions SET node_id = NULL WHERE id = 'pm-s'")
            conn.execute("UPDATE sessions SET node_id = ? WHERE id = 'pm-new'", (pm.id,))
    old = await view(app, p, PM_CRED)
    if how == "continuation":
        assert old.status_code == 401                                        # a continued session's credential is dead
    else:
        assert old.status_code == 403 and old.json()["code"] == "coordinator.not_granted"
    new = await view(app, p, CRED_NEW)
    assert new.status_code == 403 and new.json()["code"] == "coordinator.not_granted"   # the same uniform answer
    node = await detail_node(app, p, pm)
    assert node["coordinator_view"] is True                                          # the grant itself stays
    pending = node["coordinator_session"]
    assert pending["state"] == "pending_confirmation" and pending["session"]["id"] == "pm-new"
    assert pending["session"]["provider"] == "claude-code" and pending["session"]["started_at"]
    assert CRED_NEW not in json.dumps(node) and sha(CRED_NEW) not in json.dumps(node)   # never a credential
    assert (await confirm(app, p, pm, "pm-new")).status_code == 200
    assert (await view(app, p, CRED_NEW)).status_code == 200
    assert (await view(app, p, PM_CRED)).status_code in (401, 403)                  # the old one stays out


@pytest.mark.asyncio
async def test_confirming_names_the_session_the_user_saw(env):
    app, db = env
    p, q, pm, dev, rel = await world(app, db)
    await start(app, "claude", "pm-new", CRED_NEW)
    db.continue_session("pm-s", "pm-new")
    resp = await confirm(app, p, pm, "pm-s")                                          # a stale view of the node
    assert resp.status_code == 409 and resp.json()["code"] == "coordinator.session_changed"
    assert (await view(app, p, CRED_NEW)).status_code == 403
    assert (await confirm(app, p, dev, "dev-s")).status_code == 404                  # dev holds no grant here


@pytest.mark.asyncio
async def test_only_the_human_confirms_and_nothing_to_confirm_is_refused(env):
    app, db = env
    p, q, pm, dev, rel = await world(app, db)
    async with agent_client(app, "tok-claude") as agent:
        resp = await agent.post(f"/api/projects/{p.id}/nodes/{pm.id}/view-grant/confirm", json={"session_id": "pm-s"})
    assert resp.status_code == 403
    assert (await confirm(app, p, pm, "pm-s")).status_code == 200                    # already enrolled: idempotent


@pytest.mark.asyncio
async def test_pending_confirmation_and_confirmation_are_audited_once(env):
    app, db = env
    p, q, pm, dev, rel = await world(app, db)
    await start(app, "claude", "pm-new", CRED_NEW)
    db.continue_session("pm-s", "pm-new")
    for _ in range(3):
        await view(app, p, CRED_NEW)
        await detail_node(app, p, pm)
    await confirm(app, p, pm, "pm-new")
    kinds = [e["event"] for e in db.list_view_events(p.id)]
    assert kinds.count("pending_confirmation") == 1 and kinds.count("session_confirmed") == 1


# --- 2. gated providers -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_provider_without_a_verified_channel_never_gets_the_view(env):
    app, db = env
    p, q, pm, dev, rel = await world(app, db)
    db.grant_view(p.id, dev.id, "human:test")                                         # codex, with its own credential
    async with agent_client(app, "tok-codex") as codex:
        resp = await codex.get(f"/api/projects/{p.id}/coordinator-view", headers=header(DEV_CRED))
    assert resp.status_code == 403 and resp.json()["code"] == "coordinator.not_granted"
    assert (await detail_node(app, p, dev))["coordinator_supported"] is False
    assert (await detail_node(app, p, pm))["coordinator_supported"] is True


# --- 3. chains stay inside the project ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_chain_stops_at_the_project_border(env):
    app, db = env
    p, q, pm, dev, rel = await world(app, db)
    outside = make_request(db, q, rel)
    mine = make_request(db, p, rel)
    child = make_request(db, p, rel)
    db.set_chain(mine.id, outside.id, 1)
    db.set_chain(child.id, mine.id, 2)
    raw = (await view(app, p, PM_CRED)).text
    items = {r["id"]: r for r in json.loads(raw)["requests"]}
    assert items[mine.id]["parent_request_id"] == {"outside_project": True}
    assert items[mine.id]["chain_root_id"] == {"outside_project": True}
    assert items[child.id]["parent_request_id"] == mine.id and items[child.id]["chain_root_id"] == {"outside_project": True}
    assert f'"{outside.id}"' not in raw and f": {outside.id}," not in raw


# --- 4. a shared node tells nothing about its session ------------------------------------------------------


@pytest.mark.asyncio
async def test_a_shared_node_hides_its_session_state_and_the_codes_that_come_from_it(env):
    app, db = env
    p, q, pm, dev, rel = await world(app, db)
    nodes = {n["id"]: n for n in (await view(app, p, PM_CRED)).json()["nodes"]}
    shared = nodes[dev.id]
    assert shared["observed_at"] is None and shared["reachability"] == "unavailable" and shared["suspended"] is None
    assert shared["activity"] == {"state": "unavailable", "reason": "shared_node"}
    assert nodes[pm.id]["observed_at"] is not None                                    # not shared: as before
    approved = make_request(db, p, rel)
    with db._get_connection() as conn:
        conn.execute("UPDATE sessions SET state = 'busy' WHERE id = 'dev-s'")
    item = next(r for r in (await view(app, p, PM_CRED)).json()["requests"] if r["id"] == approved.id)
    assert "recipient_busy" not in item["blocking"] and "recipient_unreachable" not in item["blocking"]
    assert "shared_node" in item["blocking"]


# --- 5. deleting a project ---------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deleting_a_project_logs_each_revoked_grant(env):
    app, db = env
    p, q, pm, dev, rel = await world(app, db)
    db.grant_view(p.id, dev.id, "human:test")
    async with human_client(app) as human:
        assert (await human.delete(f"/api/projects/{p.id}")).status_code == 204
    revoked = [e for e in db.list_view_events(p.id) if e["event"] == "revoked"]
    assert sorted(e["node_id"] for e in revoked) == sorted([pm.id, dev.id])
    assert all(e["details"] == {"reason": "project_deleted"} and e["actor"].startswith("human:") for e in revoked)


# --- 6. the audit does not drown --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reads_and_refusals_are_one_row_per_session_per_minute(env):
    app, db = env
    p, q, pm, dev, rel = await world(app, db)
    for _ in range(5):
        await view(app, p, PM_CRED)
        await view(app, p, OTHER_CRED)
    events = db.list_view_events(p.id)
    reads = [e for e in events if e["event"] == "read"]
    denied = [e for e in events if e["event"] == "denied"]
    assert len(reads) == 1 and reads[0]["details"]["count"] == 5
    assert len(denied) == 1 and denied[0]["details"]["count"] == 5
    with db._get_connection() as conn:                                               # a minute later: a new row
        old = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        conn.execute("UPDATE project_view_events SET at = ? WHERE event = 'read'", (old,))
    await view(app, p, PM_CRED)
    assert len([e for e in db.list_view_events(p.id) if e["event"] == "read"]) == 2


# --- 7. nothing is cached ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_view_is_never_cached(env):
    app, db = env
    p, q, pm, dev, rel = await world(app, db)
    assert (await view(app, p, PM_CRED)).headers["cache-control"] == "no-store"
