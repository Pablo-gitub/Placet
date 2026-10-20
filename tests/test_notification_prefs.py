import asyncio
import json
from datetime import datetime, timezone
import pytest

from backend.app.config import AgentSpec, PlacetConfig
from backend.app.database import Database
from backend.app.main import create_app
from backend.app.models import Priority, RequestCreate, RequestStatus
from backend.app.webpush import WebPush
from tests.helpers import agent_client, human_client
from tests.test_session_messages import start

CONFIG = PlacetConfig(agents={
    "claude": AgentSpec(tmux_session="agent-claude", token="tok-claude", allowed_targets=["claude", "codex", "gemini", "human"]),
    "codex": AgentSpec(tmux_session="agent-codex", token="tok-codex", allowed_targets=["claude", "codex", "gemini", "human"]),
    "gemini": AgentSpec(tmux_session="agent-gemini", token="tok-gemini", allowed_targets=["claude", "codex", "gemini", "human"]),
})


@pytest.fixture
def env(tmp_path):
    db = Database(str(tmp_path / "placet.db"))
    db.init_db()
    sent_pushes = []

    def fake_send(sub, payload, urgency):
        sent_pushes.append((sub["endpoint"], json.loads(payload), urgency))
        return 201

    push = WebPush(db, subject="https://host.example", send=fake_send)
    app = create_app(db=db, config=CONFIG, web_push=push)
    return app, db, sent_pushes


@pytest.mark.asyncio
async def test_notification_settings_defaults_and_permissions(env):
    app, db, _ = env
    # Default values: needs_you=True, problems=True, turn_end="placet"
    async with human_client(app) as human:
        res = await human.get("/api/settings/notifications")
        assert res.status_code == 200
        assert res.json() == {"needs_you": True, "problems": True, "turn_end": "placet"}

        # Validation: 422 on invalid turn_end
        bad = await human.put("/api/settings/notifications", json={"turn_end": "invalid"})
        assert bad.status_code == 422

        # Validation: 422 on non-boolean
        bad_bool = await human.put("/api/settings/notifications", json={"needs_you": "not_a_bool"})
        assert bad_bool.status_code == 422

        # Successful update
        update = await human.put("/api/settings/notifications", json={
            "needs_you": False,
            "problems": False,
            "turn_end": "off",
        })
        assert update.status_code == 200
        assert update.json() == {"needs_you": False, "problems": False, "turn_end": "off"}

        # Read back persisted values
        get_res = await human.get("/api/settings/notifications")
        assert get_res.json() == {"needs_you": False, "problems": False, "turn_end": "off"}

    # Agents receive 403 on both GET and PUT
    async with agent_client(app, "tok-claude") as agent:
        res = await agent.get("/api/settings/notifications")
        assert res.status_code == 403

        res_put = await agent.put("/api/settings/notifications", json={
            "needs_you": True,
            "problems": True,
            "turn_end": "placet",
        })
        assert res_put.status_code == 403


@pytest.mark.asyncio
async def test_turn_end_placet_filter(env, monkeypatch):
    app, db, sent_pushes = env
    turn_ends = []

    async def fake_turn_end(session_id, label, last_message, cfg):
        turn_ends.append((session_id, label, last_message))
        return True

    monkeypatch.setattr("backend.app.main.send_ntfy_turn_end", fake_turn_end)

    # Subscribe to push
    sub = {"endpoint": "https://web.push.apple.com/QGx1", "keys": {"p256dh": "BPk", "auth": "au"}}
    async with human_client(app) as human:
        await human.post("/api/push/subscription", json=sub)

    await start(app, "codex", "cx1", "codex")
    db.set_session_pinned("cx1", True)

    # 1. Turn without a Placet request: MUST NOT notify under default turn_end="placet"
    async with agent_client(app, "tok-codex") as codex:
        await codex.post("/api/sessions/cx1/events", json={"event": "prompt", "title": "Manual Turn"})
        await codex.post("/api/sessions/cx1/events", json={"event": "stop", "last_message": "fatto"})

    await asyncio.sleep(0)
    assert turn_ends == []
    assert [p for p in sent_pushes if "turn" in p[1].get("tag", "")] == []

    # 2. Turn started by a Placet request: MUST notify
    async with agent_client(app, "tok-claude") as claude:
        req_res = await claude.post("/api/requests", json={
            "recipient_id": "codex",
            "prompt": "Fai questo lavoro",
        })
        req_id = req_res.json()["id"]

    db.update_request_decision(req_id, RequestStatus.APPROVED, "human")
    db.claim_injection(req_id)

    # Claim & start turn with request_id
    async with agent_client(app, "tok-codex") as codex:
        await codex.post("/api/sessions/cx1/events", json={
            "event": "prompt",
            "request_id": req_id,
            "title": "Placet Turn",
        })
        await codex.post("/api/sessions/cx1/events", json={
            "event": "stop",
            "last_message": "completato",
        })

    await asyncio.sleep(0)
    assert len(turn_ends) == 1
    assert turn_ends[0][0] == "cx1"
    assert turn_ends[0][2] == "completato"
    turn_pushes = [p for p in sent_pushes if "turn" in p[1].get("tag", "")]
    assert len(turn_pushes) == 1


@pytest.mark.asyncio
async def test_turn_end_steered_request_notifies(env, monkeypatch):
    app, db, _ = env
    turn_ends = []

    async def fake_turn_end(session_id, label, last_message, cfg):
        turn_ends.append((session_id, label, last_message))
        return True

    monkeypatch.setattr("backend.app.main.send_ntfy_turn_end", fake_turn_end)

    await start(app, "codex", "cx1", "codex")
    db.set_session_pinned("cx1", True)

    # Start a turn without request_id, then steer a request into it
    async with agent_client(app, "tok-codex") as codex:
        await codex.post("/api/sessions/cx1/events", json={"event": "prompt", "title": "Turn"})

    req = db.create_request(RequestCreate(
        sender_id="human",
        recipient_id="codex",
        prompt="urgent steer prompt",
        priority=Priority.URGENT,
        recipient_session_id="cx1",
    ))
    db.update_request_decision(req.id, RequestStatus.APPROVED, "human")
    db.claim_steer(req.id, "cx1", "agent:codex")

    async with agent_client(app, "tok-codex") as codex:
        await codex.post("/api/sessions/cx1/events", json={"event": "steered", "request_id": req.id})
        await codex.post("/api/sessions/cx1/events", json={"event": "stop", "last_message": "fatto steer"})

    await asyncio.sleep(0)
    assert len(turn_ends) == 1
    assert turn_ends[0][0] == "cx1"


@pytest.mark.asyncio
async def test_turn_end_off_and_all(env, monkeypatch):
    app, db, _ = env
    turn_ends = []

    async def fake_turn_end(session_id, label, last_message, cfg):
        turn_ends.append((session_id, label, last_message))
        return True

    monkeypatch.setattr("backend.app.main.send_ntfy_turn_end", fake_turn_end)

    await start(app, "codex", "cx1", "codex")
    db.set_session_pinned("cx1", True)

    # Set turn_end="off"
    db.set_notification_preferences({"needs_you": True, "problems": True, "turn_end": "off"})

    # Placet request turn with turn_end="off": must NOT notify
    async with agent_client(app, "tok-claude") as claude:
        req_res = await claude.post("/api/requests", json={"recipient_id": "codex", "prompt": "test"})
        req_id = req_res.json()["id"]

    db.update_request_decision(req_id, RequestStatus.APPROVED, "human")
    db.claim_injection(req_id)

    async with agent_client(app, "tok-codex") as codex:
        await codex.post("/api/sessions/cx1/events", json={"event": "prompt", "request_id": req_id})
        await codex.post("/api/sessions/cx1/events", json={"event": "stop", "last_message": "fatto"})

    await asyncio.sleep(0)
    assert turn_ends == []

    # Set turn_end="all"
    db.set_notification_preferences({"needs_you": True, "problems": True, "turn_end": "all"})

    # Turn without Placet request with turn_end="all": MUST notify
    async with agent_client(app, "tok-codex") as codex:
        await codex.post("/api/sessions/cx1/events", json={"event": "prompt"})
        await codex.post("/api/sessions/cx1/events", json={"event": "stop", "last_message": "fatto all"})

    await asyncio.sleep(0)
    assert len(turn_ends) == 1
    assert turn_ends[0][2] == "fatto all"


@pytest.mark.asyncio
async def test_needs_you_filter(env, monkeypatch):
    app, db, sent_pushes = env
    notifs = []
    alerts = []

    async def fake_notif(request, cfg, origin):
        notifs.append(request.id)
        return True

    async def fake_alert(request, cfg, problem):
        alerts.append((request.id, problem))
        return True

    monkeypatch.setattr("backend.app.main.send_ntfy_notification", fake_notif)
    monkeypatch.setattr("backend.app.main.send_ntfy_alert", fake_alert)

    # Subscribe to push
    sub = {"endpoint": "https://web.push.apple.com/QGx1", "keys": {"p256dh": "BPk", "auth": "au"}}
    async with human_client(app) as human:
        await human.post("/api/push/subscription", json=sub)

    # Set needs_you=False
    db.set_notification_preferences({"needs_you": False, "problems": True, "turn_end": "placet"})

    # 1. Question to human: suppressed
    async with agent_client(app, "tok-claude") as claude:
        q_res = await claude.post("/api/requests", json={"recipient_id": "human", "prompt": "Serve aiuto?"})
        q_id = q_res.json()["id"]

    await asyncio.sleep(0)
    assert q_id not in notifs
    assert not any(f"request-{q_id}" in p[1].get("tag", "") for p in sent_pushes)

    # 2. Prompt review: suppressed
    async with agent_client(app, "tok-claude") as claude:
        r_res = await claude.post("/api/requests", json={"recipient_id": "codex", "prompt": "Prompt da revisionare"})
        r_id = r_res.json()["id"]

    await asyncio.sleep(0)
    assert r_id not in notifs
    assert not any(f"request-{r_id}" in p[1].get("tag", "") for p in sent_pushes)

    # 3. return_waiting alert: suppressed
    req = db.create_request(RequestCreate(sender_id="claude", recipient_id="codex", prompt="fai qualcosa"))
    db.update_request_decision(req.id, RequestStatus.APPROVED, "human", allow_auto_return=False)
    db.claim_injection(req.id)
    db.confirm_injection(req.id, "agent:codex", "mcp")
    async with agent_client(app, "tok-codex") as codex:
        await codex.post(f"/api/requests/{req.id}/reply", json={"content": "ecco la risposta"})

    await asyncio.sleep(0)
    assert not any(a[1] == "return_waiting" for a in alerts)


@pytest.mark.asyncio
async def test_problems_filter(env, monkeypatch):
    app, db, sent_pushes = env
    alerts = []

    async def fake_alert(request, cfg, problem):
        alerts.append((request.id, problem))
        return True

    monkeypatch.setattr("backend.app.main.send_ntfy_alert", fake_alert)

    # Set problems=False
    db.set_notification_preferences({"needs_you": True, "problems": False, "turn_end": "placet"})

    req = db.create_request(RequestCreate(sender_id="claude", recipient_id="codex", prompt="prompt"))
    db.update_request_decision(req.id, RequestStatus.APPROVED, "human")

    # Simulate delivery failure
    from backend.app.main import ACK_TIMEOUT_SECONDS, MAX_DELIVERY_ATTEMPTS
    for _ in range(MAX_DELIVERY_ATTEMPTS):
        db.claim_injection(req.id)
        with db._get_connection() as conn:
            conn.execute("UPDATE requests SET delivering_since = datetime('now', '-100 seconds') WHERE id = ?", (req.id,))
        async with human_client(app) as human:
            await human.get("/api/requests")

    await asyncio.sleep(0)
    # Problem alert should be suppressed
    assert not any(a[1] == "delivery_failed" for a in alerts)


@pytest.mark.asyncio
async def test_push_test_never_filtered(env):
    app, db, sent_pushes = env
    sub = {"endpoint": "https://web.push.apple.com/QGx1", "keys": {"p256dh": "BPk", "auth": "au"}}
    async with human_client(app) as human:
        await human.post("/api/push/subscription", json=sub)

        # Set all to False / off
        await human.put("/api/settings/notifications", json={
            "needs_you": False,
            "problems": False,
            "turn_end": "off",
        })

        test_res = await human.post("/api/push/test")
        assert test_res.status_code == 200
        assert test_res.json()["sent"] == 1

    assert len(sent_pushes) == 1
    assert sent_pushes[0][1]["tag"] == "test"
