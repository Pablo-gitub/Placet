"""RFC-17 part 2: the coordinator's projection of a project. Metadata only, behind a verified session and a grant."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.config import AgentSpec, PlacetConfig
from backend.app.database import Database
from backend.app.main import create_app
from types import SimpleNamespace

from backend.app.models import RequestCreate, RequestStatus
from tests.helpers import agent_client, human_client

CONFIG = PlacetConfig(agents={
    "claude": AgentSpec(tmux_session="agent-claude", token="tok-claude", allowed_targets=["human", "codex"]),
    "codex": AgentSpec(tmux_session="agent-codex", token="tok-codex", allowed_targets=["human", "claude"]),
})
CRED_NEW = "plst_" + "n" * 22
PM_CRED, OTHER_CRED, DEV_CRED = "plst_" + "p" * 22, "plst_" + "o" * 22, "plst_" + "d" * 22
MARKERS = ["PROMPT-MARKER", "REPLY-MARKER", "INSTR-MARKER", "PROJINSTR-MARKER", "CONTEXT-MARKER", "OtherProjectQ",
           "tok-claude", "tok-codex", "tok-bridge", PM_CRED, DEV_CRED, "SECRET-TRANSCRIPT"]


def sha(cred):
    return hashlib.sha256(cred.encode()).hexdigest()


def header(cred):
    return {"X-Placet-Session": cred}


@pytest.fixture
def env(tmp_path):
    db = Database(str(tmp_path / "placet.db"))
    db.init_db()
    return create_app(db=db, config=CONFIG, bridge_token="tok-bridge"), db


async def start(app, agent, session, cred=None, provider="claude-code"):
    body = {"event": "start", "provider": provider, "cwd": "/p"}
    if cred:
        body["credential_sha256"] = sha(cred)
    async with agent_client(app, f"tok-{agent}") as client:
        assert (await client.post(f"/api/sessions/{session}/events", json=body)).status_code == 200


async def world(app, db):
    """Project P: PM (claude, granted) and dev (codex, shared with Q). A second claude session has no node."""
    await start(app, "claude", "pm-s", PM_CRED)
    await start(app, "claude", "other-s", OTHER_CRED)
    await start(app, "codex", "dev-s", DEV_CRED, provider="codex")
    pm, dev = db.create_node("pm", "pm-s"), db.create_node("dev", "dev-s")
    p = db.create_project("P", instructions="PROJINSTR-MARKER")
    q = db.create_project("OtherProjectQ")
    for project in (p, q):
        db.add_node_to_project(project.id, dev.id)
    db.add_node_to_project(p.id, pm.id)
    db.update_project_node_instructions(p.id, pm.id, "INSTR-MARKER")
    db.set_public_role(p.id, pm.id, "Plans and assigns the work", "human:test")
    db.set_public_role(p.id, dev.id, "Implements features", "human:test")
    kind = db.create_relation_type("develop", "Implement what is asked")
    rel = db.create_relation(pm.id, dev.id, kind.id, "manual")
    db.add_relation_to_project(p.id, rel.id)
    db.grant_view(p.id, pm.id, "human:test")
    return p, q, pm, dev, rel


def make_request(db, project, rel, snd="pm-s", rcv="dev-s", prompt="PROMPT-MARKER do it", approved=True, **extra):
    req = db.create_request(RequestCreate(sender_id="claude", recipient_id="codex", prompt=prompt, context="CONTEXT-MARKER",
                                          recipient_session_id=rcv, project_id=project.id,
                                          project_context="CONTEXT-MARKER"))
    db.link_sender_session(req.id, snd, "claude")
    db.set_request_relation(req.id, rel.id)
    if approved:
        db.update_request_decision(req.id, RequestStatus.APPROVED, actor="human:test")
    return db.get_request(req.id)


async def view(app, project, cred=None, **params):
    async with agent_client(app, "tok-claude") as claude:
        return await claude.get(f"/api/projects/{project.id}/coordinator-view", params=params,
                                headers=header(cred) if cred else None)


# --- who may read ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_declared_session_is_not_enough(env):
    app, db = env
    p, q, pm, dev, rel = await world(app, db)
    for params in ({}, {"session_id": "pm-s"}):                       # even naming the PM node's session
        resp = await view(app, p, **params)
        assert resp.status_code == 403 and resp.json()["code"] == "coordinator.credential_required"
    async with human_client(app) as human:
        assert (await human.get(f"/api/projects/{p.id}/coordinator-view")).status_code == 403


@pytest.mark.asyncio
async def test_an_invalid_credential_never_falls_back_to_declared(env):
    app, db = env
    p, q, pm, dev, rel = await world(app, db)
    resp = await view(app, p, "plst_" + "z" * 22)
    assert resp.status_code == 401 and resp.json()["code"] == "session.credential_invalid"


@pytest.mark.asyncio
async def test_another_session_of_the_same_agent_cannot_borrow_the_grant(env):
    app, db = env
    p, q, pm, dev, rel = await world(app, db)
    resp = await view(app, p, OTHER_CRED, session_id="pm-s")          # own credential, naming the PM's session
    assert resp.status_code == 403 and resp.json()["code"] == "coordinator.not_granted"
    assert (await view(app, p, PM_CRED)).status_code == 200


@pytest.mark.asyncio
async def test_a_node_without_a_grant_or_outside_the_project_sees_nothing(env):
    app, db = env
    p, q, pm, dev, rel = await world(app, db)
    assert (await view(app, p, DEV_CRED)).status_code == 401           # the codex credential is not claude's
    async with agent_client(app, "tok-codex") as codex:
        resp = await codex.get(f"/api/projects/{p.id}/coordinator-view", headers=header(DEV_CRED))
    assert resp.status_code == 403                                     # dev is in P but holds no grant
    assert (await view(app, q, PM_CRED)).status_code == 403            # the PM is not in Q
    assert (await view(app, SimpleNamespace(id=999), PM_CRED)).status_code == 403   # unknown project: same answer


@pytest.mark.asyncio
async def test_the_grant_is_rechecked_on_every_call(env):
    app, db = env
    p, q, pm, dev, rel = await world(app, db)
    assert (await view(app, p, PM_CRED)).status_code == 200
    async with human_client(app) as human:
        await human.delete(f"/api/projects/{p.id}/nodes/{pm.id}/view-grant")
    resp = await view(app, p, PM_CRED)
    assert resp.status_code == 403 and resp.json()["code"] == "coordinator.not_granted"
    db.grant_view(p.id, pm.id, "human:test")
    assert (await view(app, p, PM_CRED)).status_code == 200


@pytest.mark.asyncio
async def test_a_node_that_moved_to_another_session_keeps_nothing_until_the_user_confirms(env):
    app, db = env
    p, q, pm, dev, rel = await world(app, db)
    await start(app, "claude", "pm-new", "plst_" + "n" * 22)
    db.continue_session("pm-s", "pm-new")                                # RF-29d: the node moves on
    assert (await view(app, p, PM_CRED)).status_code == 401              # a continued session's credential is dead
    assert (await view(app, p, "plst_" + "n" * 22)).status_code == 403   # the new one is not confirmed yet


# --- what is read ----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_projection_has_the_project_nodes_relations_and_requests(env):
    app, db = env
    p, q, pm, dev, rel = await world(app, db)
    req = make_request(db, p, rel)
    body = (await view(app, p, PM_CRED)).json()
    assert body["project"]["name"] == {"origin": "human", "text": "P"} and body["project"]["id"] == p.id
    assert body["generated_at"]
    nodes = {n["id"]: n for n in body["nodes"]}
    assert nodes[pm.id]["name"]["text"] == "pm" and nodes[pm.id]["public_role"] == {"origin": "human", "text": "Plans and assigns the work"}
    assert nodes[dev.id]["provider"] == "codex" and nodes[dev.id]["reachability"] == "unavailable"   # dev is shared with Q
    assert nodes[pm.id]["reachability"] in ("reachable", "unreachable") and nodes[pm.id]["observed_at"] and "suspended" in nodes[pm.id]
    assert [(r["from_node"]["id"], r["to_node"]["id"]) for r in body["relations"]] == [(pm.id, dev.id)]
    relation = body["relations"][0]
    assert relation["type"]["name"]["text"] == "develop" and relation["type"]["description"]["text"] == "Implement what is asked"
    assert relation["approval"] == "manual" and relation["reply_approval"] in ("manual", "automatic")
    assert [r["id"] for r in body["requests"]] == [req.id]
    item = body["requests"][0]
    assert item["from_node"]["id"] == pm.id and item["to_node"]["id"] == dev.id and item["relation"]["id"] == rel.id
    assert {"approval", "delivery", "execution", "reply"} <= set(item["states"])
    assert item["created_at"] and item["chain_root_id"] == req.id and item["parent_request_id"] is None


@pytest.mark.asyncio
async def test_no_text_of_the_work_ever_appears(env):
    app, db = env
    p, q, pm, dev, rel = await world(app, db)
    done = make_request(db, p, rel, prompt="PROMPT-MARKER one")
    db.claim_injection(done.id)
    db.confirm_injection(done.id, actor="bridge", via="test")
    db.record_reply(done.id, "codex", "REPLY-MARKER finished")
    make_request(db, p, rel, prompt="PROMPT-MARKER two", approved=False)
    make_request(db, p, rel, prompt="PROMPT-MARKER three")
    raw = (await view(app, p, PM_CRED)).text
    for marker in MARKERS:
        assert marker not in raw, marker
    assert "prompt" not in json.loads(raw)["requests"][0] and "response" not in json.loads(raw)["requests"][0]


@pytest.mark.asyncio
async def test_unapproved_requests_are_metadata_only_with_a_reason(env):
    app, db = env
    p, q, pm, dev, rel = await world(app, db)
    pending = make_request(db, p, rel, approved=False)
    item = (await view(app, p, PM_CRED)).json()["requests"][0]
    assert item["id"] == pending.id and item["states"]["approval"]["state"] == "pending"
    assert item["blocking"] == ["awaiting_approval"]
    assert "prompt" not in item and "PROMPT-MARKER" not in json.dumps(item)


@pytest.mark.asyncio
async def test_what_belongs_to_other_projects_is_not_shown(env):
    app, db = env
    p, q, pm, dev, rel = await world(app, db)
    mine, theirs = make_request(db, p, rel), make_request(db, q, rel)
    body = (await view(app, p, PM_CRED)).json()
    assert [r["id"] for r in body["requests"]] == [mine.id]
    assert "OtherProjectQ" not in json.dumps(body) and "shared_with" not in json.dumps(body)
    nodes = {n["id"]: n for n in body["nodes"]}
    assert nodes[dev.id]["activity"] == {"state": "unavailable", "reason": "shared_node"}   # dev also works for Q
    assert nodes[pm.id]["activity"]["state"] in ("idle", "busy", "unknown")                 # pm is only in P


@pytest.mark.asyncio
async def test_a_relation_only_in_another_project_is_not_listed(env):
    app, db = env
    p, q, pm, dev, rel = await world(app, db)
    kind = db.create_relation_type("review", "Review it")
    other = db.create_relation(dev.id, pm.id, kind.id, "manual")
    db.add_relation_to_project(q.id, other.id)
    body = (await view(app, p, PM_CRED)).json()
    assert [r["id"] for r in body["relations"]] == [rel.id]


@pytest.mark.asyncio
async def test_old_observations_are_marked_stale_and_unknown_is_explicit(env):
    app, db = env
    p, q, pm, dev, rel = await world(app, db)
    await start(app, "codex", "solo-s", None, provider="codex")
    solo = db.create_node("solo", "solo-s")
    db.add_node_to_project(p.id, solo.id)                                    # only in P: its session state may be shown
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    with db._get_connection() as conn:
        conn.execute("UPDATE sessions SET last_seen_at = ? WHERE id = 'solo-s'", (old,))
    await start(app, "claude", "gone-s", None)
    lone = db.create_node("lone", "gone-s")
    db.add_node_to_project(p.id, lone.id)
    with db._get_connection() as conn:
        conn.execute("UPDATE sessions SET node_id = NULL WHERE id = 'gone-s'")                    # a node with no session
    nodes = {n["id"]: n for n in (await view(app, p, PM_CRED)).json()["nodes"]}
    assert nodes[solo.id]["stale"] is True and nodes[solo.id]["observed_at"] == old
    assert nodes[solo.id]["activity"] == {"state": "unknown", "reason": "stale"}
    assert nodes[pm.id]["stale"] is False
    assert nodes[lone.id]["reachability"] == "unknown" and nodes[lone.id]["observed_at"] is None
    assert nodes[lone.id]["activity"]["state"] == "unknown"


@pytest.mark.asyncio
async def test_pagination_walks_every_request_once_and_nodes_come_first(env):
    app, db = env
    p, q, pm, dev, rel = await world(app, db)
    ids = [make_request(db, p, rel).id for _ in range(5)]
    seen, cursor, pages = [], None, 0
    while True:
        body = (await view(app, p, PM_CRED, limit=2, **({"cursor": cursor} if cursor else {}))).json()
        pages += 1
        assert (len(body["nodes"]) > 0) == (pages == 1)
        seen += [r["id"] for r in body["requests"]]
        cursor = body["next_cursor"]
        if not cursor:
            break
    assert seen == ids and pages == 3
    assert (await view(app, p, PM_CRED, cursor="not-a-cursor")).status_code == 422
    assert (await view(app, p, PM_CRED, limit=0)).status_code == 422
    assert (await view(app, p, PM_CRED, limit=1000)).status_code == 422


@pytest.mark.asyncio
async def test_a_chain_names_its_root_and_parent(env):
    app, db = env
    p, q, pm, dev, rel = await world(app, db)
    root = make_request(db, p, rel)
    child = make_request(db, p, rel)
    grand = make_request(db, p, rel)
    db.set_chain(child.id, root.id, 1)
    db.set_chain(grand.id, child.id, 2)
    items = {r["id"]: r for r in (await view(app, p, PM_CRED)).json()["requests"]}
    assert (items[grand.id]["parent_request_id"], items[grand.id]["chain_root_id"], items[grand.id]["chain_hops"]) == (child.id, root.id, 2)


@pytest.mark.asyncio
async def test_a_request_between_a_project_node_and_an_outside_node_hides_the_outsider(env):
    app, db = env
    p, q, pm, dev, rel = await world(app, db)
    await start(app, "codex", "out-s", None, provider="codex")
    outsider = db.create_node("outsider-secret-name", "out-s")
    item = make_request(db, p, rel, rcv="out-s")
    body = (await view(app, p, PM_CRED)).json()
    shown = next(r for r in body["requests"] if r["id"] == item.id)
    assert shown["to_node"] == {"id": None, "name": None, "in_project": False}
    assert "outsider-secret-name" not in json.dumps(body)


# --- reading is not sending ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_view_grant_gives_no_route_and_no_automatic_delivery(env):
    app, db = env
    p, q, pm, dev, rel = await world(app, db)
    await start(app, "codex", "dev2-s", None, provider="codex")
    stranger = db.create_node("stranger", "dev2-s")
    db.add_node_to_project(p.id, stranger.id)                      # in the project, but no relation from the PM
    assert (await view(app, p, PM_CRED)).status_code == 200
    async with agent_client(app, "tok-claude") as claude:
        resp = await claude.post("/api/requests", json={"recipient_id": "codex", "prompt": "do it",
                                                        "recipient_session_id": "dev2-s", "sender_session_id": "pm-s"})
    # Without a relation the send is what it always was: a manual prompt the user must approve, on no route.
    assert resp.status_code == 201, resp.text
    sent = resp.json()
    assert sent["status"] == "PENDING" and sent["relation_id"] is None and sent["delivery_policy"] == "manual"


@pytest.mark.asyncio
async def test_reads_and_refusals_are_audited_without_the_content(env):
    app, db = env
    p, q, pm, dev, rel = await world(app, db)
    make_request(db, p, rel)
    await view(app, p, PM_CRED)
    await view(app, p, OTHER_CRED)
    events = db.list_view_events(p.id)
    kinds = [e["event"] for e in events]
    assert "read" in kinds and "denied" in kinds
    assert "PROMPT-MARKER" not in str(events) and OTHER_CRED not in str(events)


@pytest.mark.asyncio
async def test_suspended_codex_node_reachability_is_wakeable(env):
    app, db = env
    await start(app, "codex", "cx-solo", DEV_CRED, provider="codex")
    db.suspend_session("cx-solo")
    session = db.get_session("cx-solo")
    assert session.suspended_at is not None and not session.reachable

    cx_node = db.create_node("cx", "cx-solo")
    p = db.create_project("ProjectCodex")
    db.add_node_to_project(p.id, cx_node.id)

    from backend.app.coordinator_view import Projection
    view_data = Projection(db, p.id).node(cx_node)
    assert view_data["reachability"] == "wakeable"
