import os
import asyncio
import contextlib
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Literal, Optional, Dict, Set
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks, status, Request, Depends
from fastapi.middleware.cors import CORSMiddleware

from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse


from backend.app.auth import (
    Authenticator,
    Principal,
    load_allowed_users,
    require_agent,
    require_bridge,
    require_human,
)
from backend.app.config import PlacetConfig, load_agents_config
from backend.app.database import Database
from backend.app.models import (
    RequestCreate,
    RequestEvent,
    RequestModel,
    RequestStatus,
    Priority,
    QuestionType,
    RequestDecision,
    SessionModel,
    INBOX_WAIT_SECONDS,
    RESULT_GRACE_SECONDS,
    RESULT_MAX_AGE_SECONDS,
    ACK_TIMEOUT_SECONDS,
    AGENT_BUSY_TTL_SECONDS,
    MAX_DELIVERY_ATTEMPTS,
    decision_error,
    is_settled,
)
from backend.app import envelopes
from backend.app.notifier import NtfyConfig, send_ntfy_alert, send_ntfy_notification

logger = logging.getLogger(__name__)

# Upper bound for a single sleep while long-polling: changes made by other
# processes (the bridge writes to SQLite directly) are not signalled, so the
# waiter re-reads the database at least this often.
LONG_POLL_RECHECK_SECONDS = 5.0
EXPIRY_SWEEP_SECONDS = 30.0


class SessionEventPayload(BaseModel):
    """What an agent CLI / desktop app hook reports about one of its sessions."""
    event: Literal["start", "prompt", "stop", "tool", "end"]
    provider: Optional[str] = None
    entrypoint: Optional[str] = None
    cwd: Optional[str] = None
    title: Optional[str] = None
    tmux_session: Optional[str] = None
    # prompt: the Placet request the prompt carries ("[Placet Request #N").
    # tool:   the request created by a Placet MCP call in this session.
    request_id: Optional[int] = None
    # stop: the agent's last message, used as implicit reply if needed.
    last_message: Optional[str] = None


class AgentStatePayload(BaseModel):
    state: Literal["busy", "idle"]
    request_id: Optional[int] = None
    last_message: Optional[str] = None


class ReplyPayload(BaseModel):
    content: str
    # Accepted for backward compatibility; the responder is always the
    # authenticated agent.
    responder_id: Optional[str] = None


def _parse_origins(raw: Optional[str]) -> List[str]:
    return [o.strip() for o in (raw or "").split(",") if o.strip()]


def create_app(
    db: Optional[Database] = None,
    config: Optional[PlacetConfig] = None,
    ntfy: Optional[NtfyConfig] = None,
    allowed_users: Optional[List[str]] = None,
    cors_origins: Optional[List[str]] = None,
    bridge_token: Optional[str] = None,
) -> FastAPI:
    # Waiters registered by long-polling calls, keyed by request id.
    request_waiters: Dict[int, Set[asyncio.Event]] = {}
    sse_subscribers: List[asyncio.Queue] = []

    # Session inboxes waiting for something to change.
    inbox_waiters: Set[asyncio.Event] = set()

    def _broadcast(item):
        for q in list(sse_subscribers):
            try:
                q.put_nowait(item)
            except Exception:
                pass

    def notify_change(req_id: int):
        # Must run on the event loop thread: all endpoints touching it are async.
        for ev in request_waiters.get(req_id, ()):
            ev.set()
        for ev in list(inbox_waiters):
            ev.set()
        _broadcast(("request", req_id))

    def notify_session(session_id: str):
        for ev in list(inbox_waiters):
            ev.set()
        _broadcast(("session", session_id))

    background_alerts: Set[asyncio.Task] = set()

    def alert(req_id: int, problem: str):
        req = db.get_request(req_id)
        if req is None:
            return
        try:
            task = asyncio.get_running_loop().create_task(send_ntfy_alert(req, ntfy, problem))
        except RuntimeError:
            return  # no running loop (sync caller): the PWA still shows it
        background_alerts.add(task)
        task.add_done_callback(background_alerts.discard)

    def execution_timeout(agent_id: str) -> float:
        spec = config.get_agent(agent_id)
        return spec.execution_timeout_seconds if spec else 1800

    def expire_and_notify():
        """Time-based transitions: expiry, unacked injections, stalled work."""
        for req_id in db.expire_stale_requests():
            notify_change(req_id)
        requeued, failed = db.requeue_unacked(ACK_TIMEOUT_SECONDS, MAX_DELIVERY_ATTEMPTS)
        for req_id in requeued:
            notify_change(req_id)
        for req_id in failed:
            notify_change(req_id)
            alert(req_id, "consegna fallita")
        for req_id in db.stall_executing(execution_timeout):
            notify_change(req_id)
            alert(req_id, "nessuna risposta, richiesta bloccata")

    async def expiry_sweeper():
        while True:
            await asyncio.sleep(EXPIRY_SWEEP_SECONDS)
            try:
                expire_and_notify()
            except Exception as e:
                logger.error(f"Expiry sweep failed: {e}")

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        task = asyncio.create_task(expiry_sweeper())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def wait_until_settled(request_id: int, wait_seconds: float) -> Optional[RequestModel]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait_seconds
        while True:
            ev = asyncio.Event()
            # Register before reading, so a change between the read and the
            # wait is not lost.
            request_waiters.setdefault(request_id, set()).add(ev)
            try:
                expire_and_notify()
                current = db.get_request(request_id)
                remaining = deadline - loop.time()
                if current is None or is_settled(current) or remaining <= 0:
                    return current
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(
                        ev.wait(), timeout=min(remaining, LONG_POLL_RECHECK_SECONDS)
                    )
            finally:
                waiters = request_waiters.get(request_id)
                if waiters is not None:
                    waiters.discard(ev)
                    if not waiters:
                        request_waiters.pop(request_id, None)

    def deliver_to(who: Principal, req: Optional[RequestModel]) -> Optional[RequestModel]:
        """
        When a settled outcome is returned to its sender, record it as
        delivered so the bridge does not inject it into the session again.
        """
        if req is not None and who.id == req.sender_id and is_settled(req) and req.delivered_at is None:
            db.mark_delivered(req.id, actor=who.actor, via="api")
            req = db.get_request(req.id)
        return req

    app = FastAPI(title="Placet API", version="1.0.0", lifespan=lifespan)

    if db is None:
        db = Database(os.environ.get("PLACET_DB_PATH", "placet.db"))
        db.init_db()

    if config is None:
        config = load_agents_config()

    if ntfy is None:
        ntfy = NtfyConfig.from_env()

    if allowed_users is None:
        allowed_users = load_allowed_users()

    if cors_origins is None:
        cors_origins = _parse_origins(os.environ.get("PLACET_CORS_ORIGINS"))

    # The PWA is served by the backend itself (same origin): CORS is only
    # needed for a separate dev server, and only for listed origins.
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["Authorization", "Content-Type"],
        )

    if bridge_token is None:
        bridge_token = os.environ.get("PLACET_BRIDGE_TOKEN", "")

    authenticator = Authenticator(config, allowed_users, bridge_token=bridge_token)

    def principal(request: Request) -> Principal:
        return authenticator.identify(request)

    def get_or_404(request_id: int) -> RequestModel:
        req = db.get_request(request_id)
        if not req:
            raise HTTPException(status_code=404, detail="Request not found")
        return req

    def check_target_session(session_id: str, agent_id: str) -> None:
        session = db.get_session(session_id)
        if session is None or session.agent_id != agent_id or session.ended_at is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Session {session_id} is not an open session of {agent_id}.",
            )

    def require_party(who: Principal, req: RequestModel) -> None:
        """The human sees everything; an agent only its own requests."""
        if who.is_human or who.id in (req.sender_id, req.recipient_id):
            return
        # 404 rather than 403: do not reveal other agents' requests.
        raise HTTPException(status_code=404, detail="Request not found")

    @app.get("/api/health")
    def health():
        return {"status": "ok", "app": "Placet"}

    @app.get("/api/agents")
    def list_agents(who: Principal = Depends(principal)):
        return {
            "agents": {k: v.model_dump() for k, v in config.agents.items()},
            "rules": {k: v.model_dump() for k, v in config.rules.items()},
            "description": config.description_markdown,
        }

    @app.post("/api/requests", status_code=status.HTTP_201_CREATED)
    async def create_request(
        req: RequestCreate,
        background_tasks: BackgroundTasks,
        wait_seconds: int = Query(0, ge=0, le=600),
        who: Principal = Depends(principal),
    ):
        require_agent(who)
        if req.sender_id is not None and req.sender_id != who.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Token belongs to {who.id}, not to {req.sender_id}.",
            )
        req = req.model_copy(update={"sender_id": who.id})

        # Validate allowed route
        if not config.is_route_allowed(req.sender_id, req.recipient_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Route from {req.sender_id} to {req.recipient_id} not allowed by AGENTS.md rules.",
            )

        if req.recipient_id == "human":
            if req.question_type == QuestionType.PROMPT_REVIEW:
                # Older clients sent open questions to the human as prompt_review.
                req = req.model_copy(update={"question_type": QuestionType.CONFIRMATION})
        elif req.question_type != QuestionType.PROMPT_REVIEW:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Requests to an agent must be of type prompt_review.",
            )

        if req.recipient_session_id is not None:
            check_target_session(req.recipient_session_id, req.recipient_id)

        if req.priority is None:
            req = req.model_copy(update={
                "priority": Priority.HIGH if req.recipient_id == "human" else Priority.NORMAL
            })

        # Check if approval required
        needs_approval = config.requires_approval(req.sender_id, req.recipient_id)

        created = db.create_request(req, actor=who.actor)
        # The session making a request is in the middle of a turn: if exactly
        # one session of this agent is busy, it is the sender. The PostToolUse
        # hook links it too, but must not be the only way.
        busy = [s for s in db.list_sessions(agent_id=who.id) if s.state == "busy"]
        if len(busy) == 1 and db.link_sender_session(created.id, busy[0].id, who.id):
            created = db.get_request(created.id)

        if not needs_approval:
            # Auto-approval
            created = db.update_request_decision(
                request_id=created.id,
                status=RequestStatus.APPROVED,
                actor="system:auto-routing",
            )
        else:
            # Send mobile notification
            background_tasks.add_task(
                send_ntfy_notification,
                request=created,
                cfg=ntfy,
            )
        notify_change(created.id)

        if wait_seconds > 0:
            return deliver_to(who, await wait_until_settled(created.id, float(wait_seconds)))
        return created

    @app.get("/api/requests", response_model=List[RequestModel])
    async def list_requests(
        status: Optional[RequestStatus] = None,
        limit: int = Query(50, ge=1, le=200),
        who: Principal = Depends(principal),
    ):
        require_human(who)
        expire_and_notify()
        return db.list_requests(status=status, limit=limit)

    @app.get("/api/requests/{request_id}", response_model=RequestModel)
    async def get_request(
        request_id: int,
        wait_seconds: int = Query(0, ge=0, le=600),
        who: Principal = Depends(principal),
    ):
        require_party(who, get_or_404(request_id))
        if wait_seconds > 0:
            req = await wait_until_settled(request_id, float(wait_seconds))
        else:
            expire_and_notify()
            req = db.get_request(request_id)
        if not req:
            raise HTTPException(status_code=404, detail="Request not found")
        return deliver_to(who, req)

    @app.get("/api/requests/{request_id}/events", response_model=List[RequestEvent])
    async def request_events(request_id: int, who: Principal = Depends(principal)):
        require_party(who, get_or_404(request_id))
        return db.list_events(request_id)

    @app.post("/api/requests/{request_id}/decision", response_model=RequestModel)
    async def decide_request(
        request_id: int,
        decision: RequestDecision,
        who: Principal = Depends(principal),
    ):
        require_human(who)
        expire_and_notify()
        req = get_or_404(request_id)

        if req.status != RequestStatus.PENDING:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Request {request_id} is in {req.status.value} state, not in PENDING state.",
            )

        problem = decision_error(req, decision)
        if problem:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=problem)
        if decision.recipient_session_id is not None:
            check_target_session(decision.recipient_session_id, req.recipient_id)

        updated = db.update_request_decision(
            request_id=request_id,
            status=decision.status,
            edited_prompt=decision.edited_prompt,
            selected_options=decision.selected_options,
            custom_text=decision.custom_text,
            actor=who.actor,
        )
        if updated is not None and decision.recipient_session_id is not None:
            db.set_recipient_session(request_id, decision.recipient_session_id)
            updated = db.get_request(request_id)
        if updated is None:
            # State changed between the check and the update (e.g. expired or cancelled).
            current = db.get_request(request_id)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Request {request_id} is in {current.status.value} state, not in PENDING state.",
            )
        notify_change(request_id)
        return updated

    @app.post("/api/requests/{request_id}/reply", response_model=RequestModel)
    async def reply_request(
        request_id: int,
        payload: ReplyPayload,
        who: Principal = Depends(principal),
    ):
        require_agent(who)
        expire_and_notify()
        req = get_or_404(request_id)
        require_party(who, req)
        if who.id != req.recipient_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Only {req.recipient_id} can reply to request {request_id}.",
            )
        if payload.responder_id is not None and payload.responder_id != who.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Token belongs to {who.id}, not to {payload.responder_id}.",
            )

        if req.status not in (RequestStatus.APPROVED, RequestStatus.DELIVERING, RequestStatus.EXECUTING):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Request {request_id} is in {req.status.value} state, not in APPROVED, DELIVERING or EXECUTING state.",
            )

        completed = db.record_reply(
            request_id=request_id,
            responder_id=who.id,
            content=payload.content,
            actor=who.actor,
        )
        if completed is None:
            current = db.get_request(request_id)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Request {request_id} is in {current.status.value} state, not in APPROVED, DELIVERING or EXECUTING state.",
            )
        notify_change(request_id)
        return completed

    @app.delete("/api/requests/{request_id}")
    async def cancel_request(request_id: int, who: Principal = Depends(principal)):
        expire_and_notify()
        req = get_or_404(request_id)
        require_party(who, req)
        if not who.is_human and who.id != req.sender_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Only {req.sender_id} or the human can cancel request {request_id}.",
            )

        if req.status != RequestStatus.PENDING:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot cancel request in {req.status.value} state (must be PENDING).",
            )

        if not db.cancel_request(
            request_id, by_sender=(who.id == req.sender_id), actor=who.actor
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot cancel request {request_id}: its state changed concurrently.",
            )
        notify_change(request_id)
        return {"status": "CANCELLED", "id": request_id}


    # --- Agent acknowledgements and turn state ------------------------------

    def ack_request(who: Principal, request_id: int, via: str) -> RequestModel:
        req = get_or_404(request_id)
        require_party(who, req)
        if who.id != req.recipient_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Only {req.recipient_id} can acknowledge request {request_id}.",
            )
        if req.status == RequestStatus.DELIVERING:
            updated = db.confirm_injection(request_id, actor=who.actor, via=via)
            if updated is not None:
                notify_change(request_id)
                return updated
            req = get_or_404(request_id)
        if req.status in (RequestStatus.EXECUTING, RequestStatus.COMPLETED):
            return req  # already acknowledged: idempotent
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Request {request_id} is in {req.status.value} state, it cannot be acknowledged.",
        )

    @app.post("/api/requests/{request_id}/ack", response_model=RequestModel)
    async def ack(request_id: int, who: Principal = Depends(principal)):
        """The recipient confirms it received an injected prompt (placet_ack)."""
        require_agent(who)
        expire_and_notify()
        return ack_request(who, request_id, via="mcp")

    @app.post("/api/agents/me/state")
    async def agent_state(payload: AgentStatePayload, who: Principal = Depends(principal)):
        """
        Turn events from the agent's CLI hooks.
        busy: a turn started; with request_id, the turn was started by that
              injected prompt, which is thereby acknowledged.
        idle: the turn ended; if it was started by an injected request that
              got no placet_reply, last_message becomes its implicit reply.
        """
        require_agent(who)
        expire_and_notify()
        result: Dict[str, object] = {"agent": who.id, "state": payload.state}
        if payload.state == "busy":
            turn_request_id = None
            if payload.request_id is not None:
                try:
                    ack_request(who, payload.request_id, via="hook")
                    turn_request_id = payload.request_id
                except HTTPException as e:
                    result["ack_error"] = e.detail
            db.set_agent_state(who.id, "busy", turn_request_id)
            result["turn_request_id"] = turn_request_id
            return result

        previous = db.get_agent_states().get(who.id, {})
        db.set_agent_state(who.id, "idle", None)
        replied = implicit_reply(who, previous.get("turn_request_id"), payload.last_message)
        if replied is not None:
            result["implicit_reply_for"] = replied
        return result

    def implicit_reply(who: Principal, turn_request_id: Optional[int], last_message: Optional[str]):
        """
        At the end of a turn started by a Placet request that got no
        placet_reply, the agent's last message becomes an implicit reply.
        """
        if turn_request_id is None or not last_message:
            return None
        req = db.get_request(turn_request_id)
        if req is None or req.recipient_id != who.id or req.status != RequestStatus.EXECUTING:
            return None  # already replied (or no longer waiting)
        completed = db.record_reply(
            turn_request_id, who.id, last_message, actor=f"{who.actor}:hook", implicit=True
        )
        if completed is None:
            return None
        notify_change(turn_request_id)
        return turn_request_id

    # --- Agent sessions (hooks of CLIs and desktop apps) ----------------------

    def own_session(who: Principal, session_id: str) -> SessionModel:
        require_agent(who)
        session = db.get_session(session_id)
        if session is None or session.agent_id != who.id:
            raise HTTPException(status_code=404, detail="Session not found")
        return session

    @app.post("/api/sessions/{session_id}/events")
    async def session_event(
        session_id: str, payload: SessionEventPayload, who: Principal = Depends(principal)
    ):
        """
        Lifecycle of an agent session, reported by its hooks: start, a prompt
        (turn starts; a Placet prompt is thereby acknowledged), stop (turn
        ends; possible implicit reply), a Placet tool call (links the request
        to this session), end.
        """
        require_agent(who)
        if not db.upsert_session(
            session_id, who.id, provider=payload.provider, entrypoint=payload.entrypoint,
            cwd=payload.cwd, title=payload.title, tmux_session=payload.tmux_session,
        ):
            raise HTTPException(status_code=404, detail="Session not found")
        session = db.get_session(session_id)
        result: Dict[str, object] = {"session": session_id, "event": payload.event}

        if payload.event == "start":
            db.set_session_state(session_id, "idle")
        elif payload.event == "prompt":
            turn_request_id = None
            if payload.request_id is not None:
                try:
                    ack_request(who, payload.request_id, via="hook")
                    turn_request_id = payload.request_id
                except HTTPException as e:
                    result["ack_error"] = e.detail
            db.set_session_state(session_id, "busy", turn_request_id)
            result["turn_request_id"] = turn_request_id
        elif payload.event == "stop":
            db.set_session_state(session_id, "idle")
            replied = implicit_reply(who, session.turn_request_id, payload.last_message)
            if replied is not None:
                result["implicit_reply_for"] = replied
        elif payload.event == "tool" and payload.request_id is not None:
            result["linked"] = db.link_sender_session(payload.request_id, session_id, who.id)
        elif payload.event == "end":
            db.end_session(session_id)

        notify_session(session_id)
        return result

    @app.get("/api/sessions/{session_id}/inbox")
    async def session_inbox(
        session_id: str,
        wait_seconds: int = Query(0, ge=0, le=INBOX_WAIT_SECONDS),
        who: Principal = Depends(principal),
    ):
        """
        Long-polled by the session's background waiter. Returns the next
        message to wake the session with, or 204 when there is none in time.
        Nothing is handed out while the session is in the middle of a turn.
        """
        own_session(who, session_id)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait_seconds
        first = True
        while True:
            ev = asyncio.Event()
            inbox_waiters.add(ev)
            try:
                expire_and_notify()
                db.touch_waiter(session_id)
                if first:
                    notify_session(session_id)  # now reachable
                    first = False
                session = db.get_session(session_id)
                if session is None or session.ended_at is not None:
                    return Response(status_code=status.HTTP_410_GONE)
                taken = db.take_inbox_message(session)
                if taken is not None:
                    kind, req = taken
                    notify_change(req.id)
                    ack_mode = getattr(config.get_agent(who.id), "ack_mode", "mcp")
                    text = (
                        envelopes.outcome(req, channel="hook") if kind == "outcome"
                        else envelopes.injection(req, ack_mode=ack_mode, channel="hook")
                    )
                    return {"kind": kind, "request_id": req.id, "text": text}
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return Response(status_code=status.HTTP_204_NO_CONTENT)
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(ev.wait(), timeout=min(remaining, LONG_POLL_RECHECK_SECONDS))
            finally:
                inbox_waiters.discard(ev)

    @app.get("/api/sessions", response_model=List[SessionModel])
    async def list_sessions(
        agent_id: Optional[str] = None,
        include_ended: bool = False,
        who: Principal = Depends(principal),
    ):
        """Sessions of all agents: for the PWA and for agents choosing a target."""
        if who.kind == "bridge":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not for the bridge.")
        return db.list_sessions(agent_id=agent_id, include_ended=include_ended)

    # --- Local bridge (tmux injection and outcome delivery) -----------------

    @app.get("/api/bridge/work")
    async def bridge_work(who: Principal = Depends(principal)):
        """
        What the bridge should do now: approved agent-to-agent requests to
        inject (oldest first, at most one in flight per recipient), settled
        outcomes to deliver to the sender's session, and the turn state the
        agents declared through their hooks.
        """
        require_bridge(who)
        expire_and_notify()
        now = datetime.now(timezone.utc)
        results = db.list_undelivered_results(
            settled_before=now - timedelta(seconds=RESULT_GRACE_SECONDS),
            settled_after=now - timedelta(seconds=RESULT_MAX_AGE_SECONDS),
        )
        injections, results, busy = db.tmux_work_filter(
            db.injections_ready(),
            results,
            lambda agent_id: getattr(config.get_agent(agent_id), "tmux_session", None),
        )
        return {
            "injections": injections,
            "results": results,
            "busy_agents": sorted(set(db.busy_agents(AGENT_BUSY_TTL_SECONDS)) | set(busy)),
        }

    @app.post("/api/bridge/requests/{request_id}/claim", response_model=RequestModel)
    async def bridge_claim_injection(request_id: int, who: Principal = Depends(principal)):
        """APPROVED -> DELIVERING, before sending; 409 if no longer APPROVED."""
        require_bridge(who)
        get_or_404(request_id)
        claimed = db.claim_injection(request_id, actor=who.actor)
        if claimed is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Request {request_id} is not APPROVED.",
            )
        notify_change(request_id)
        return claimed

    @app.delete("/api/bridge/requests/{request_id}/claim")
    async def bridge_release_injection(request_id: int, who: Principal = Depends(principal)):
        """Sending failed: back to APPROVED without counting the attempt."""
        require_bridge(who)
        get_or_404(request_id)
        released = db.release_injection(request_id, actor=who.actor)
        if released:
            notify_change(request_id)
        return {"id": request_id, "released": released}

    @app.post("/api/bridge/requests/{request_id}/injected", response_model=RequestModel)
    async def bridge_injected(request_id: int, who: Principal = Depends(principal)):
        """For agents with ack_mode none: a successful send counts as received."""
        require_bridge(who)
        get_or_404(request_id)
        updated = db.confirm_injection(request_id, actor=who.actor, via="bridge")
        if updated is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Request {request_id} is not DELIVERING.",
            )
        notify_change(request_id)
        return updated

    @app.post("/api/bridge/requests/{request_id}/delivered")
    async def bridge_claim_delivery(request_id: int, who: Principal = Depends(principal)):
        """Claims the delivery of an outcome; 409 if it was already delivered."""
        require_bridge(who)
        get_or_404(request_id)
        if not db.mark_delivered(request_id, actor=who.actor, via="tmux"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Outcome of request {request_id} was already delivered.",
            )
        return {"id": request_id, "delivered": True}

    @app.delete("/api/bridge/requests/{request_id}/delivered")
    async def bridge_release_delivery(request_id: int, who: Principal = Depends(principal)):
        """Undoes a claim after a failed injection, so the bridge retries."""
        require_bridge(who)
        get_or_404(request_id)
        db.clear_delivered(request_id, actor=who.actor)
        return {"id": request_id, "delivered": False}

    @app.get("/api/events")
    async def sse_events(who: Principal = Depends(principal)):
        require_human(who)
        queue: asyncio.Queue = asyncio.Queue()
        sse_subscribers.append(queue)

        async def event_generator():
            try:
                while True:
                    kind, key = await queue.get()
                    if kind == "session":
                        session = db.get_session(key)
                        if session:
                            yield {"event": "session_update", "data": session.model_dump_json()}
                        continue
                    req = db.get_request(key)
                    if req:
                        yield {
                            "event": "request_update",
                            "data": req.model_dump_json(),
                        }
            except asyncio.CancelledError:
                sse_subscribers.remove(queue)

        return EventSourceResponse(event_generator())

    # Serve built PWA if frontend/dist exists
    dist_path = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
    if dist_path.exists():
        assets_path = dist_path / "assets"
        if assets_path.exists():
            app.mount("/assets", StaticFiles(directory=assets_path), name="assets")

        @app.get("/{full_path:path}")
        async def serve_pwa(full_path: str):
            if full_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="API route not found")
            file_candidate = dist_path / full_path
            if file_candidate.is_file():
                return FileResponse(file_candidate)
            return FileResponse(dist_path / "index.html")

    return app



# Default app instance
app = create_app()
