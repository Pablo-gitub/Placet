"""RFC-03..07, RFC-13: relation types, relations A -> B and their policies."""
import pytest

from backend.app.config import AgentSpec, PlacetConfig
from backend.app.database import Database
from backend.app.main import create_app
from tests.helpers import agent_client, human_client

CONFIG = PlacetConfig(agents={
    "claude": AgentSpec(tmux_session="agent-claude", token="tok-claude", allowed_targets=["human", "codex"]),
    "codex": AgentSpec(tmux_session="agent-codex", token="tok-codex", allowed_targets=["human", "claude"]),
})


@pytest.fixture
def env(tmp_path):
    db = Database(str(tmp_path / "placet.db"))
    db.init_db()
    return create_app(db=db, config=CONFIG), db


async def event(app, agent, session_id, body):
    async with agent_client(app, f"tok-{agent}") as client:
        assert (await client.post(f"/api/sessions/{session_id}/events", json=body)).status_code == 200


async def two_nodes(app, db):
    """A busy Claude session (the sender) and a Codex one, both named."""
    await event(app, "claude", "cl", {"event": "start", "provider": "claude-code", "cwd": "/p"})
    await event(app, "claude", "cl", {"event": "prompt", "provider": "claude-code"})
    await event(app, "codex", "cx", {"event": "start", "provider": "codex", "cwd": "/p"})
    return db.create_node("planner", "cl"), db.create_node("builder", "cx")


async def send(app, type_id=None, agent="claude", target="codex", session="cx"):
    async with agent_client(app, f"tok-{agent}") as client:
        body = {"recipient_id": target, "prompt": "do it", "recipient_session_id": session}
        if type_id is not None:
            body["relation_type_id"] = type_id
        resp = await client.post("/api/requests", json=body)
        assert resp.status_code == 201, resp.text
        return resp.json()


@pytest.mark.asyncio
async def test_without_relation_delivery_is_manual(env):
    app, db = env
    await two_nodes(app, db)
    req = await send(app)
    assert req["status"] == "PENDING" and req["relation_id"] is None


@pytest.mark.asyncio
async def test_automatic_relation_skips_review_manual_does_not(env):
    app, db = env
    a, b = await two_nodes(app, db)
    review = db.create_relation_type("review", "Ask for a code review")
    rel = db.create_relation(a.id, b.id, review.id, "automatic")
    req = await send(app)
    assert req["status"] == "APPROVED" and req["relation_id"] == rel.id
    # RFC-16: the reply policy defaults to the outbound one.
    assert req["allow_auto_return"]
    events = db.list_events(req["id"])
    assert any(e.actor == f"system:relation#{rel.id}" for e in events)
    db.set_relation_approval(rel.id, "manual")
    req = await send(app)
    assert req["status"] == "PENDING" and req["relation_id"] == rel.id


@pytest.mark.asyncio
async def test_several_types_need_the_type(env):
    app, db = env
    a, b = await two_nodes(app, db)
    review = db.create_relation_type("review", "")
    build = db.create_relation_type("build", "")
    db.create_relation(a.id, b.id, review.id, "automatic")
    db.create_relation(a.id, b.id, build.id, "manual")
    # Placet never guesses between types: without one, review by the user.
    assert (await send(app))["status"] == "PENDING"
    assert (await send(app, review.id))["status"] == "APPROVED"
    assert (await send(app, build.id))["status"] == "PENDING"


@pytest.mark.asyncio
async def test_reply_policy_is_the_relation_own_setting(env):
    """RFC-16: the reply follows the relation's reply setting, not the reverse relation."""
    app, db = env
    a, b = await two_nodes(app, db)
    review = db.create_relation_type("review", "")
    report = db.create_relation_type("report", "")
    rel = db.create_relation(a.id, b.id, review.id, "automatic")
    # The reply default follows the outbound setting.
    assert rel.reply_approval == "automatic"
    # A reverse relation of another type no longer matters.
    db.create_relation(b.id, a.id, report.id, "manual")
    assert (await send(app))["allow_auto_return"] is True
    db.set_relation_reply_approval(rel.id, "manual")
    assert (await send(app))["allow_auto_return"] is False
    # A manual outbound relation keeps its own reply setting after approval.
    db.set_relation_approval(rel.id, "manual")
    db.set_relation_reply_approval(rel.id, "automatic")
    req = await send(app)
    async with human_client(app) as human:
        resp = await human.post(f"/api/requests/{req['id']}/decision", json={"status": "APPROVED"})
        assert resp.status_code == 200 and resp.json()["allow_auto_return"] is True


@pytest.mark.asyncio
async def test_manual_relation_replies_with_approval_by_default(env):
    app, db = env
    a, b = await two_nodes(app, db)
    review = db.create_relation_type("review", "")
    rel = db.create_relation(a.id, b.id, review.id, "manual")
    assert rel.reply_approval == "manual"
    # An automatic reverse relation of the same type does not change it either.
    db.create_relation(b.id, a.id, review.id, "automatic")
    req = await send(app)
    async with human_client(app) as human:
        resp = await human.post(f"/api/requests/{req['id']}/decision", json={"status": "APPROVED"})
        assert resp.status_code == 200 and resp.json()["allow_auto_return"] is False


@pytest.mark.asyncio
async def test_reply_policy_api_and_listing(env):
    app, db = env
    a, b = await two_nodes(app, db)
    review = db.create_relation_type("review", "")
    async with human_client(app) as human:
        body = {"from_node": a.id, "to_node": b.id, "type_id": review.id, "approval": "automatic",
                "reply_approval": "manual"}
        resp = await human.post("/api/relations", json=body)
        assert resp.status_code == 201 and resp.json()["reply_approval"] == "manual"
        rel_id = resp.json()["id"]
        resp = await human.patch(f"/api/relations/{rel_id}", json={"reply_approval": "automatic"})
        assert resp.status_code == 200
        assert (resp.json()["approval"], resp.json()["reply_approval"]) == ("automatic", "automatic")
        resp = await human.patch(f"/api/relations/{rel_id}", json={})
        assert resp.status_code == 422
        resp = await human.patch(f"/api/relations/{rel_id}", json={"reply_approval": "maybe"})
        assert resp.status_code == 422
    async with agent_client(app, "tok-claude") as client:
        listed = (await client.get("/api/agents/me/relations", params={"session_id": "cl"})).json()
    assert listed["relations"][0]["reply_policy"] == "automatic"


@pytest.mark.asyncio
async def test_existing_relations_migrate_with_reply_equal_to_outbound(env):
    import sqlite3
    app, db = env
    path = db.db_path
    a, b = await two_nodes(app, db)
    t = db.create_relation_type("review", "")
    auto = db.create_relation(a.id, b.id, t.id, "automatic")
    manual = db.create_relation(b.id, a.id, t.id, "manual")
    # Simulate a database created before RFC-16.
    with sqlite3.connect(path) as conn:
        conn.execute("DELETE FROM schema_migrations WHERE name = 'relation_reply_approval_v1'")
        conn.execute("UPDATE relations SET reply_approval = 'manual'")
    db.init_db()
    assert db.get_relation(auto.id).reply_approval == "automatic"
    assert db.get_relation(manual.id).reply_approval == "manual"


@pytest.mark.asyncio
async def test_relation_api(env):
    app, db = env
    a, b = await two_nodes(app, db)
    async with human_client(app) as human:
        resp = await human.post("/api/relation-types", json={"name": "review", "description": "Code review"})
        assert resp.status_code == 201
        type_id = resp.json()["id"]
        resp = await human.post("/api/relation-types", json={"name": "Review"})
        assert resp.status_code == 409 and resp.json()["code"] == "relation_type.name_taken"
        body = {"from_node": a.id, "to_node": b.id, "type_id": type_id, "approval": "automatic"}
        resp = await human.post("/api/relations", json=body)
        assert resp.status_code == 201
        rel = resp.json()
        assert (rel["from_name"], rel["to_name"], rel["type_name"]) == ("planner", "builder", "review")
        assert (await human.post("/api/relations", json=body)).json()["code"] == "relation.exists"
        resp = await human.post("/api/relations", json={**body, "to_node": a.id})
        assert resp.status_code == 422 and resp.json()["code"] == "relation.self"
        resp = await human.delete(f"/api/relation-types/{type_id}")
        assert resp.status_code == 409 and resp.json()["code"] == "relation_type.in_use"
        resp = await human.patch(f"/api/relations/{rel['id']}", json={"approval": "manual"})
        assert resp.json()["approval"] == "manual"
        assert len((await human.get(f"/api/relations?node_id={b.id}")).json()) == 1
        # Deleting a node removes its relations.
        assert (await human.delete(f"/api/nodes/{b.id}")).status_code == 204
        assert (await human.get("/api/relations")).json() == []
        assert (await human.delete(f"/api/relation-types/{type_id}")).status_code == 204
    async with agent_client(app, "tok-claude") as client:
        assert (await client.get("/api/relations")).status_code in (401, 403, 404)
        assert (await client.post("/api/relation-types", json={"name": "x"})).status_code in (401, 403, 404)


@pytest.mark.asyncio
async def test_the_user_redirects_a_prompt_to_another_agent(env):
    app, db = env
    a, b = await two_nodes(app, db)
    await event(app, "claude", "cl2", {"event": "start", "provider": "claude-code", "cwd": "/q"})
    review = db.create_relation_type("review", "")
    rel = db.create_relation(a.id, b.id, review.id, "manual")
    req = await send(app)
    assert req["relation_id"] == rel.id
    async with human_client(app) as human:
        # The sender's own session is never a target.
        resp = await human.post(f"/api/requests/{req['id']}/decision",
                                json={"status": "APPROVED", "recipient_session_id": "cl"})
        assert resp.status_code == 422
        assert db.get_request(req["id"]).recipient_id == "codex"
        resp = await human.post(f"/api/requests/{req['id']}/decision",
                                json={"status": "APPROVED", "recipient_session_id": "cl2"})
        assert resp.status_code == 200, resp.text
        out = resp.json()
    assert (out["recipient_id"], out["recipient_session_id"], out["relation_id"]) == ("claude", "cl2", None)
    assert out["status"] == "APPROVED" and not out["allow_auto_return"]
    redirected = [e for e in db.list_events(req["id"]) if e.event == "redirected"]
    assert len(redirected) == 1 and redirected[0].details["from"] == "codex"


@pytest.mark.asyncio
async def test_the_user_forwards_a_reply_to_another_session(env):
    from backend.app import envelopes
    app, db = env
    await two_nodes(app, db)
    await event(app, "claude", "cl2", {"event": "start", "provider": "claude-code", "cwd": "/q"})
    req = await send(app)
    async with human_client(app) as human:
        await human.post(f"/api/requests/{req['id']}/decision", json={"status": "APPROVED"})
    db.claim_injection(req["id"])
    db.confirm_injection(req["id"], actor="bridge", via="test")
    async with agent_client(app, "tok-codex") as client:
        resp = await client.post(f"/api/requests/{req['id']}/reply", json={"content": "the report"})
        assert resp.status_code == 200, resp.text
    async with human_client(app) as human:
        url = f"/api/requests/{req['id']}/return-decision"
        # Neither the sender nor the session that replied.
        for sid in ("cl", "cx"):
            resp = await human.post(url, json={"approve": True, "forward_session_id": sid})
            assert resp.status_code == 422
        resp = await human.post(url, json={"approve": True, "forward_session_id": "cl2",
                                           "edited_response": "the report, trimmed"})
        assert resp.status_code == 200, resp.text
        out = resp.json()
        assert out["return_decision"] == "forwarded"
        again = await human.post(url, json={"approve": True})
        assert again.status_code == 409
    fwd = db.get_request(out["return_forward_id"])
    assert (fwd.sender_id, fwd.recipient_id, fwd.recipient_session_id) == ("codex", "claude", "cl2")
    assert fwd.prompt == "the report, trimmed" and fwd.status.value == "APPROVED"
    assert fwd.sender_session_id == "cx"
    # The sender gets a notice, never the content.
    original = db.get_request(req["id"])
    notice = envelopes.outcome(original, language="en")
    assert "forwarded the reply" in notice and "the report" not in notice
    async with agent_client(app, "tok-claude") as client:
        seen = (await client.get(f"/api/requests/{req['id']}")).json()
        assert seen["return_decision"] == "forwarded"
        assert seen["response"] is None or "report" not in str(seen["response"])


async def post(app, body, agent="claude"):
    async with agent_client(app, f"tok-{agent}") as client:
        return await client.post("/api/requests", json={"prompt": "do it", **body})


@pytest.mark.asyncio
async def test_agents_address_nodes_and_read_the_rule_each_time(env):
    app, db = env
    a, b = await two_nodes(app, db)
    review = db.create_relation_type("review", "Ask for a code review")
    db.create_relation(a.id, b.id, review.id, "automatic")
    db.set_relation_rule("If in doubt, ask the human first.")
    before = len(db.list_requests())
    resp = await post(app, {"target_node": "Builder", "relation_type": "review"})
    assert resp.status_code == 428
    body = resp.json()
    assert body["rule"] == "If in doubt, ask the human first." and body["relation"]["to"] == "builder"
    assert len(db.list_requests()) == before  # nothing was created
    resp = await post(app, {"target_node": "builder", "rule_acknowledged": True})
    assert resp.status_code == 201, resp.text
    out = resp.json()
    assert out["status"] == "APPROVED" and out["delivery_policy"] == "automatic"
    assert (out["recipient_id"], out["recipient_session_id"]) == ("codex", "cx")
    # Each send needs it again.
    assert (await post(app, {"target_node": "builder"})).status_code == 428
    # Manual relations and ad hoc requests never ask for it.
    db.set_relation_approval(db.find_relation(a.id, b.id).id, "manual")
    resp = await post(app, {"target_node": "builder"})
    assert resp.status_code == 201 and resp.json()["delivery_policy"] == "manual"
    assert (await post(app, {"target_node": "nobody"})).json()["code"] == "node.not_found"
    assert (await post(app, {"target_node": "builder", "relation_type": "x"})).status_code == 404
    assert (await post(app, {})).json()["code"] == "request.no_recipient"


@pytest.mark.asyncio
async def test_a_relation_opens_a_route_agents_md_does_not_list(env, tmp_path):
    db = Database(str(tmp_path / "other.db"))
    db.init_db()
    config = PlacetConfig(agents={
        "claude": AgentSpec(tmux_session="a", token="tok-claude", allowed_targets=["human"]),
        "codex": AgentSpec(tmux_session="b", token="tok-codex", allowed_targets=["human"]),
    })
    app = create_app(db=db, config=config)
    a, b = await two_nodes(app, db)
    assert (await post(app, {"target_node": "builder"})).status_code == 409  # no relation: refused, never ad hoc
    db.create_relation(a.id, b.id, db.create_relation_type("review", "").id, "manual")
    assert (await post(app, {"target_node": "builder"})).status_code == 201


@pytest.mark.asyncio
async def test_agents_list_their_relations(env):
    app, db = env
    a, b = await two_nodes(app, db)
    review = db.create_relation_type("review", "Ask for a code review")
    db.create_relation(a.id, b.id, review.id, "automatic")
    db.create_relation(b.id, a.id, review.id, "manual")
    db.set_relation_rule("Ask first.")
    async with agent_client(app, "tok-claude") as client:
        out = (await client.get("/api/agents/me/relations")).json()
    assert out["node"] == "planner" and out["rule"] == "Ask first."
    [rel] = out["relations"]
    assert (rel["to"], rel["type"], rel["approval"], rel["reply_policy"]) == ("builder", "review", "automatic", "automatic")
    assert rel["description"] == "Ask for a code review" and rel["target_agent"] == "codex"
    # Another agent's session is never read.
    async with agent_client(app, "tok-codex") as client:
        out = (await client.get("/api/agents/me/relations", params={"session_id": "cl"})).json()
    assert out["session_id"] is None and out["relations"] == []
    async with human_client(app) as human:
        assert (await human.put("/api/settings/relation-rule", json={"rule": " New "})).json() == {"rule": "New"}


def test_the_recipient_sees_whether_the_user_reviewed(env):
    from backend.app import envelopes
    from backend.app.models import RequestModel
    from datetime import datetime, timezone
    base = dict(id=1, sender_id="claude", recipient_id="codex", question_type="prompt_review",
                prompt="p", created_at=datetime.now(timezone.utc))
    auto = envelopes.injection(RequestModel(**base, approval="automatic"), language="en")
    assert "without human review" in auto and "placet_ask_human" in auto
    assert "reviewed and approved" in envelopes.injection(RequestModel(**base, approval="manual"), language="en")
    edited = RequestModel(**base, approval="manual", edited_prompt="q")
    assert "edited" in envelopes.injection(edited, language="en")


async def ping_pong(app, db):
    a, b = await two_nodes(app, db)
    t = db.create_relation_type("review", "")
    db.create_relation(a.id, b.id, t.id, "automatic")
    db.create_relation(b.id, a.id, t.id, "automatic")
    return a, b


async def hop(app, db, agent, session, target, parent):
    """`session` runs the turn of `parent` and sends on to `target`."""
    db.set_session_state(session, "busy", parent)
    return await send(app, agent=agent, target=target, session=target_session[target])

target_session = {"codex": "cx", "claude": "cl"}


@pytest.mark.asyncio
async def test_chains_stop_after_three_automatic_hops(env):
    app, db = env
    await ping_pong(app, db)
    first = await send(app)
    assert first["status"] == "APPROVED" and first["chain_hops"] == 1
    second = await hop(app, db, "codex", "cx", "claude", first["id"])
    third = await hop(app, db, "claude", "cl", "codex", second["id"])
    assert (second["chain_hops"], third["chain_hops"]) == (2, 3) and third["status"] == "APPROVED"
    fourth = await hop(app, db, "codex", "cx", "claude", third["id"])
    assert fourth["status"] == "PENDING" and fourth["automation_limit"] == "hops"
    assert fourth["parent_request_id"] == third["id"] and fourth["chain_hops"] == 0
    assert any(e.event == "automation_limited" for e in db.list_events(fourth["id"]))
    # The user approves it: the next hop starts a new count.
    async with human_client(app) as human:
        await human.post(f"/api/requests/{fourth['id']}/decision", json={"status": "APPROVED"})
    fifth = await hop(app, db, "claude", "cl", "codex", fourth["id"])
    assert fifth["status"] == "APPROVED" and fifth["chain_hops"] == 1


@pytest.mark.asyncio
async def test_a_question_to_the_user_resets_the_chain(env):
    app, db = env
    await ping_pong(app, db)
    db.set_automation(max_hops=1)
    first = await send(app)
    db.set_session_state("cx", "busy", first["id"])
    async with agent_client(app, "tok-codex") as client:
        q = (await client.post("/api/requests", json={"recipient_id": "human", "prompt": "ok?"})).json()
    async with human_client(app) as human:
        await human.post(f"/api/requests/{q['id']}/decision", json={"status": "APPROVED"})
    second = await send(app, agent="codex", target="claude", session="cl")
    assert second["status"] == "APPROVED" and second["chain_hops"] == 1


@pytest.mark.asyncio
async def test_hourly_limit_per_relation(env):
    app, db = env
    await ping_pong(app, db)
    db.set_automation(max_per_hour=2)
    assert [(await send(app))["status"] for _ in range(3)] == ["APPROVED", "APPROVED", "PENDING"]


@pytest.mark.asyncio
async def test_stop_all_holds_queued_prompts_until_resumed(env):
    app, db = env
    await ping_pong(app, db)
    queued = await send(app)
    async with human_client(app) as human:
        state = (await human.get("/api/automation")).json()
        assert state["paused"] is False and state["automatic_relations"] == 2
        stopped = (await human.post("/api/automation/stop")).json()
        assert stopped["paused"] is True and stopped["held"] == [queued["id"]]
        assert db.get_request(queued["id"]).status.value == "PENDING"
        paused = await send(app)
        assert paused["status"] == "PENDING" and paused["automation_limit"] == "paused"
        await human.post("/api/automation/resume")
        assert (await send(app))["status"] == "APPROVED"
        resp = await human.put("/api/automation", json={"max_hops": 5, "max_per_hour": 50})
        assert (resp.json()["max_hops"], resp.json()["max_per_hour"]) == (5, 50)
        assert (await human.put("/api/automation", json={"max_hops": 0})).status_code == 422
    async with agent_client(app, "tok-claude") as client:
        assert (await client.post("/api/automation/stop")).status_code in (401, 403, 404)


@pytest.mark.asyncio
async def test_the_sender_names_its_session_when_several_are_busy(env):
    app, db = env
    a, b = await two_nodes(app, db)
    review = db.create_relation_type("review", "Ask for a code review")
    db.create_relation(a.id, b.id, review.id, "automatic")
    # A second busy Claude session: the backend can no longer guess the sender.
    await event(app, "claude", "cl2", {"event": "start", "provider": "claude-code", "cwd": "/q"})
    await event(app, "claude", "cl2", {"event": "prompt", "provider": "claude-code"})
    before = len(db.list_requests())
    resp = await post(app, {"target_node": "builder"})
    assert resp.status_code == 409 and resp.json()["code"] == "relation.sender_unknown"
    assert len(db.list_requests()) == before  # never a silent ad hoc request
    resp = await post(app, {"target_node": "builder", "sender_session_id": "cl"})
    assert resp.status_code == 201, resp.text
    out = resp.json()
    assert out["delivery_policy"] == "automatic" and out["sender_session_id"] == "cl"
    assert out["relation_id"] == db.find_relation(a.id, b.id).id
    # Only a session of the calling agent.
    resp = await post(app, {"target_node": "builder", "sender_session_id": "cx"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_a_node_without_a_relation_is_refused(env):
    app, db = env
    await two_nodes(app, db)
    before = len(db.list_requests())
    resp = await post(app, {"target_node": "builder"})
    assert resp.status_code == 409 and resp.json()["code"] == "relation.not_found"
    assert len(db.list_requests()) == before
