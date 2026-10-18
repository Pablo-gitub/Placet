from dataclasses import replace
import os
import json
import asyncio
import base64
import contextlib
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Literal, Optional, Dict, Set
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks, status, Request, Depends
from fastapi import Path as FastAPIPath
from fastapi.middleware.cors import CORSMiddleware

from fastapi.responses import Response
from pydantic import BaseModel, Field
from backend.app.errors import ProductError, product_error_response
from backend.app.i18n import text
from backend.app.pwa import mount_pwa
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
    OutcomeDecision,
    SessionMessage,
    SessionModel,
    BRIDGE_PROVIDERS,
    SUSPENDING_PROVIDERS,
    STEER_PROVIDERS,
    INBOX_WAIT_SECONDS,
    RESULT_GRACE_SECONDS,
    RESULT_MAX_AGE_SECONDS,
    ACK_TIMEOUT_SECONDS,
    AGENT_BUSY_TTL_SECONDS,
    MAX_DELIVERY_ATTEMPTS,
    decision_problem,
    is_settled,
)
from backend.app import envelopes
from backend.app.upload_limits import MessageBodyLimit
from backend.app.bridge_jobs import BridgeJobs, BridgeOffline, BridgeTimeout
from backend.app.notifier import NtfyConfig, send_ntfy_alert, send_ntfy_notification, send_ntfy_turn_end, session_label

logger = logging.getLogger(__name__)

# Upper bound for a single sleep while long-polling: changes made by other
# processes (the bridge writes to SQLite directly) are not signalled, so the
# waiter re-reads the database at least this often.
LONG_POLL_RECHECK_SECONDS = 5.0
EXPIRY_SWEEP_SECONDS = 30.0


class SessionEventPayload(BaseModel):
    """What an agent CLI / desktop app hook reports about one of its sessions."""
    event: Literal["start", "prompt", "stop", "tool", "end", "steered"]
    # The hook script and its version: the provenance of execution proofs.
    adapter: Optional[str] = Field(None, max_length=64)
    # Set by the hooks' local queue: an event forwarded twice is applied once.
    event_id: Optional[str] = Field(None, max_length=64)
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


class TitlesReport(BaseModel):
    titles: Dict[str, str] = Field(default_factory=dict, max_length=500)


# RF-43: one "turn finished" notification per followed session at most this often.
TURN_END_NOTIFY_GAP = 60.0
# RF-09: a question to the user waits at least this long before returning PENDING.
MIN_HUMAN_WAIT_SECONDS = 60



class FoldersReport(BaseModel):
    checked: List[str] = Field(default_factory=list, max_length=1000)
    excluded: List[str] = Field(default_factory=list, max_length=1000)


EXCLUDED_DETAIL = "This project is excluded from Placet (.placet-off)."


class BackgroundLanguage(BaseModel):
    language: Literal["en", "it"]


class QueueRelease(BaseModel):
    reason: Literal["finished", "queue_next"]


class PinPayload(BaseModel):
    pinned: bool


class BridgeJobResult(BaseModel):
    """What the bridge reports for a job: a result, or an error code."""
    ok: bool
    page: Optional[dict] = None
    error: Optional[str] = None
    message: Optional[str] = None


# Conversation pages (RF-30): what the bridge's error codes mean for the PWA.
TRANSCRIPT_ERRORS = {
    "not_found": (404, "The conversation was not found on this PC."),
    "unsupported": (422, "Reading conversations is not supported for this provider."),
    "invalid": (400, "Invalid session."),
    "excluded": (409, "This project is excluded from Placet (.placet-off)."),
}
ATTACHMENT_ERRORS = {
    **TRANSCRIPT_ERRORS,
    "not_found": (404, "The file is no longer available on the PC."),
    "forbidden": (403, "This file cannot be displayed."),
    "unsupported": (415, "Unsupported format: use JPEG, PNG, WebP images or PDF files."),
    "too_large": (413, "The attachment is too large."),
}
ATTACHMENT_ID_PATTERN = r"^\d+:\d+:\d+$"
TRANSCRIPT_MAX_LIMIT = 200
BRIDGE_JOBS_WAIT_SECONDS = 30


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
    transcripts_local: Optional[bool] = None,
) -> FastAPI:
    # Waiters registered by long-polling calls, keyed by request id.
    request_waiters: Dict[int, Set[asyncio.Event]] = {}
    sse_subscribers: List[asyncio.Queue] = []

    # Session inboxes waiting for something to change.
    inbox_waiters: Set[asyncio.Event] = set()

    # Work for the bridge that someone is waiting for (conversation pages).
    bridge_jobs = BridgeJobs()
    if transcripts_local is None:
        # Single-process development (run_all.py): the backend runs as the
        # user and reads conversations itself. Never in production.
        transcripts_local = os.environ.get("PLACET_TRANSCRIPTS_LOCAL") == "1"

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
        req = db.get_request(req_id)
        if (req is not None and req.status == RequestStatus.COMPLETED
                and req.sender_id != "human" and req.recipient_id != "human"
                and req.delivered_at is None and not req.allow_auto_return
                and req.return_decision is None):
            alert(req_id, "return_waiting")

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
            task = asyncio.get_running_loop().create_task(send_ntfy_alert(req, replace(ntfy, language=db.get_background_language()), text(f"alerts.{problem}", db.get_background_language())))
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
            alert(req_id, "delivery_failed")
        for req_id in db.unconfirmed_to_uncertain(ACK_TIMEOUT_SECONDS):
            notify_change(req_id)
            alert(req_id, "delivery_uncertain")
        for req_id in db.waiting_for_unreachable_sessions():
            notify_change(req_id)
            alert(req_id, "unreachable")
        for req_id in db.unconfirmed_starts():
            notify_change(req_id)
            alert(req_id, "start_unconfirmed")
        for req_id in db.stall_executing(execution_timeout):
            notify_change(req_id)
            stalled = db.get_request(req_id)
            if stalled is None:
                continue
            if stalled.recipient_session_id and stalled.queue_released_at is None:
                # Still holding the session's queue: only the user moves it on.
                alert(req_id, "queue_held")
            elif stalled.question_type != QuestionType.MESSAGE:
                # The user's own messages need no reply: the answer is in the chat.
                alert(req_id, "stalled")

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
        if (req is not None and not who.is_human and req.recipient_id != "human"
                and req.status == RequestStatus.COMPLETED):
            if not req.allow_auto_return and req.return_decision != "approved":
                return req.model_copy(update={"response": None, "return_text": None})
            if req.return_text is not None and req.response is not None:
                req = req.model_copy(update={"response": req.response.model_copy(update={"content": req.return_text})})
        if req is not None and who.id == req.sender_id and is_settled(req) and req.delivered_at is None:
            db.mark_delivered(req.id, actor=who.actor, via="api")
            req = req.model_copy(update={"delivered_at": db.get_request(req.id).delivered_at})
        return req

    app = FastAPI(title="Placet API", version="1.0.0", lifespan=lifespan)
    app.add_exception_handler(ProductError, product_error_response)

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

    app.add_middleware(MessageBodyLimit)

    # The PWA is served by the backend itself (same origin): CORS is only
    # needed for a separate dev server, and only for listed origins.
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "DELETE"],
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

    def created_sender_session(who: Principal) -> Optional[str]:
        """The session about to create a request: the only busy one of the agent."""
        busy = [s for s in db.list_sessions(agent_id=who.id) if s.state == "busy"]
        return busy[0].id if len(busy) == 1 else None

    def origin_of(req: RequestModel) -> Optional[str]:
        """The sender session, as the notification names it: 'Claude · <title>'."""
        if not req.sender_session_id:
            return None
        session = db.get_session(req.sender_session_id)
        if session is None:
            return None
        return session_label(session.provider, session.title, session.cwd) or None

    # A followed session's turn ends: at most one quiet notification a minute each.
    turn_end_notified: Dict[str, float] = {}

    def notify_turn_end(session: SessionModel, last_message: Optional[str]) -> None:
        if not session.pinned or session.excluded:
            return
        loop_time = asyncio.get_running_loop().time()
        if loop_time - turn_end_notified.get(session.id, -TURN_END_NOTIFY_GAP) < TURN_END_NOTIFY_GAP:
            return
        turn_end_notified[session.id] = loop_time
        task = asyncio.get_running_loop().create_task(send_ntfy_turn_end(
            session.id, session_label(session.provider, session.title, session.cwd), last_message, replace(ntfy, language=db.get_background_language())))
        background_alerts.add(task)
        task.add_done_callback(background_alerts.discard)

    def check_target_session(session_id: str, agent_id: str) -> None:
        session = db.get_session(session_id)
        if session is None or session.agent_id != agent_id or session.ended_at is not None:
            raise ProductError(
                422, "session.unavailable", f"Session {session_id} is not an open session of {agent_id}.",
            )
        if session.excluded:
            raise ProductError(
                422, "session.excluded", f"Session {session_id} belongs to a project excluded from Placet (.placet-off).",
            )

    def require_party(who: Principal, req: RequestModel) -> None:
        """The human sees everything; an agent only its own requests."""
        if who.is_human or who.id in (req.sender_id, req.recipient_id):
            return
        # 404 rather than 403: do not reveal other agents' requests.
        raise HTTPException(status_code=404, detail="Request not found")

    @app.get("/api/settings/language", response_model=BackgroundLanguage)
    async def background_language(response: Response, who: Principal = Depends(principal)):
        require_human(who)
        response.headers["Cache-Control"] = "no-store"
        return {"language": db.get_background_language()}

    @app.put("/api/settings/language", response_model=BackgroundLanguage)
    async def set_background_language(payload: BackgroundLanguage, response: Response, who: Principal = Depends(principal)):
        require_human(who)
        db.set_background_language(payload.language)
        response.headers["Cache-Control"] = "no-store"
        return {"language": db.get_background_language()}

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

        if req.question_type == QuestionType.MESSAGE:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Only the user writes messages to a session.",
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
            if req.recipient_session_id == created_sender_session(who):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="A session cannot send a request to itself.",
                )

        if req.priority is None:
            req = req.model_copy(update={
                "priority": Priority.HIGH if req.recipient_id == "human" else Priority.NORMAL
            })

        # Check if approval required
        needs_approval = config.requires_approval(req.sender_id, req.recipient_id)

        target_warning = None
        if req.recipient_session_id is not None:
            target = db.get_session(req.recipient_session_id)
            if target is not None and not target.reachable and not target.tmux_session:
                target_warning = (
                    f"Session {req.recipient_session_id} is not reachable now: the request "
                    "will be delivered only if it comes back before expiring."
                )

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
                cfg=replace(ntfy, language=db.get_background_language()),
                origin=origin_of(created),
            )
        notify_change(created.id)

        extra: Dict[str, object] = {}
        if target_warning:
            extra["warning"] = target_warning
        if wait_seconds > 0:
            if created.recipient_id == "human" and wait_seconds < MIN_HUMAN_WAIT_SECONDS:
                # RF-09: the user needs time to read and answer; a few seconds
                # would only turn every question into a wake-up.
                wait_seconds = MIN_HUMAN_WAIT_SECONDS
                extra["wait_seconds"] = wait_seconds
            created = deliver_to(who, await wait_until_settled(created.id, float(wait_seconds)))
            if not is_settled(created):
                extra["note"] = text("envelope.pending", db.get_background_language())
        if extra:
            return {**created.model_dump(mode="json"), **extra}
        return created

    @app.get("/api/requests", response_model=List[RequestModel])
    async def list_requests(
        status: Optional[RequestStatus] = None,
        limit: int = Query(50, ge=1, le=200),
        who: Principal = Depends(principal),
    ):
        require_human(who)
        expire_and_notify()
        return db.list_requests(status=status, limit=limit, hide_excluded=True)

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
            raise ProductError(
                409, "decision.not_pending",
                f"Request {request_id} is in {req.status.value} state, not in PENDING state.",
                {"id": request_id},
            )

        problem = decision_problem(req, decision)
        if problem:
            raise ProductError(422, problem[0], problem[1])
        if decision.recipient_session_id is not None:
            check_target_session(decision.recipient_session_id, req.recipient_id)
            if decision.recipient_session_id == req.sender_session_id:
                raise ProductError(422, "decision.self_target", "A session cannot send a request to itself.")
        if decision.status == RequestStatus.APPROVED and decision.delivery_mode == "now":
            target_id = decision.recipient_session_id or req.recipient_session_id
            target = db.get_session(target_id) if target_id else None
            if (req.question_type != QuestionType.PROMPT_REVIEW or req.recipient_id == "human"
                    or target is None or target.provider not in STEER_PROVIDERS):
                raise ProductError(422, "decision.now_target", "Now requires a specific Claude or Antigravity session.")
            check_target_session(target_id, req.recipient_id)

        updated = db.update_request_decision(
            request_id=request_id,
            status=decision.status,
            edited_prompt=decision.edited_prompt,
            selected_options=decision.selected_options,
            custom_text=decision.custom_text,
            actor=who.actor,
            allow_auto_return=decision.allow_auto_return,
            delivery_mode=decision.delivery_mode if decision.status == RequestStatus.APPROVED and req.recipient_id != "human" else None,
            recipient_session_id=decision.recipient_session_id,
        )
        if updated is None:
            # State changed between the check and the update (e.g. expired or cancelled).
            current = db.get_request(request_id)
            raise ProductError(
                409, "decision.not_pending",
                f"Request {request_id} is in {current.status.value} state, not in PENDING state.",
                {"id": request_id},
            )
        notify_change(request_id)
        return updated

    @app.post("/api/requests/{request_id}/return-decision", response_model=RequestModel)
    async def decide_return(request_id: int, payload: OutcomeDecision,
                            who: Principal = Depends(principal)):
        require_human(who)
        get_or_404(request_id)
        if payload.edited_response is not None and not payload.edited_response.strip():
            raise ProductError(422, "return.empty", "The reply cannot be empty.")
        updated = db.decide_return(request_id, payload.approve, payload.edited_response, who.actor)
        if updated is None:
            raise ProductError(409, "return.unavailable", "The reply has already been decided, forwarded, or is no longer available.")
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


    @app.post("/api/requests/{request_id}/delivery/retry", response_model=RequestModel)
    async def retry_delivery(request_id: int, who: Principal = Depends(principal)):
        """The user sends an uncertain delivery again (it may arrive twice)."""
        require_human(who)
        get_or_404(request_id)
        if not db.retry_delivery(request_id, actor=who.actor):
            raise ProductError(409, "delivery.retry_unavailable", "Delivery is no longer uncertain.")
        notify_change(request_id)
        return get_or_404(request_id)

    @app.post("/api/requests/{request_id}/delivery/cancel", response_model=RequestModel)
    async def cancel_delivery(request_id: int, who: Principal = Depends(principal)):
        """The user withdraws a message still waiting, or whose delivery is uncertain."""
        require_human(who)
        get_or_404(request_id)
        if not db.cancel_delivery(request_id, actor=who.actor):
            raise ProductError(409, "delivery.cancel_unavailable", "This message can no longer be cancelled.")
        notify_change(request_id)
        return get_or_404(request_id)

    @app.post("/api/requests/{request_id}/queue/release", response_model=RequestModel)
    async def release_queue(request_id: int, payload: QueueRelease, who: Principal = Depends(principal)):
        """
        The user moves a session's queue past a delivered request whose end
        Placet cannot see: "finished" (they say it ended) or "queue_next".
        """
        require_human(who)
        get_or_404(request_id)
        if not db.release_queue(request_id, actor=who.actor, reason=payload.reason):
            raise ProductError(409, "delivery.release_unavailable", "This request is no longer holding the session queue.")
        notify_change(request_id)
        return get_or_404(request_id)

    # --- Agent acknowledgements and turn state ------------------------------

    def ack_request(who: Principal, request_id: int, via: str) -> RequestModel:
        """The recipient received the prompt (hook at turn start, or placet_ack)."""
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
        if payload.event_id and db.session_event_seen(payload.event_id):
            return {"session": session_id, "event": payload.event, "duplicate": True}
        if not db.upsert_session(
            session_id, who.id, provider=payload.provider, entrypoint=payload.entrypoint,
            cwd=payload.cwd, title=payload.title, tmux_session=payload.tmux_session,
        ):
            raise HTTPException(status_code=404, detail="Session not found")
        session = db.get_session(session_id)
        result: Dict[str, object] = {"session": session_id, "event": payload.event}
        source = " ".join(filter(None, [payload.provider or session.provider, payload.adapter or "hook"]))

        if payload.event in ("start", "prompt"):
            # Only a real start or turn brings a session back: a late event
            # of another kind must not reopen it.
            db.reopen_session(session_id)
        if payload.event == "start":
            db.set_session_state(session_id, "idle")
        elif payload.event == "prompt":
            turn_request_id = None
            if payload.request_id is not None:
                try:
                    ack_request(who, payload.request_id, via="hook")
                    turn_request_id = payload.request_id
                    db.set_execution(turn_request_id, "started", actor=f"{who.actor}:hook", source=source)
                except HTTPException as e:
                    result["ack_error"] = e.detail
            elif session.state == "busy" and session.turn_request_id is not None:
                # Another message joined the turn a Placet request started
                # (typed in the app, or queued): the turn still belongs to the
                # request, but its last answer may not be the reply to it.
                turn_request_id = session.turn_request_id
                db.mark_turn_mixed(turn_request_id)
            db.set_session_state(session_id, "busy", turn_request_id)
            result["turn_request_id"] = turn_request_id
        elif payload.event == "stop":
            db.set_session_state(session_id, "idle")
            if session.state == "busy":
                notify_turn_end(session, payload.last_message)
            if session.turn_request_id is not None:
                db.set_execution(session.turn_request_id, "completed", actor=f"{who.actor}:hook",
                                 source=source)
                notify_change(session.turn_request_id)
            replied = implicit_reply(who, session.turn_request_id, payload.last_message)
            if replied is not None:
                result["implicit_reply_for"] = replied
            for steered_id in db.steered_in_turn(session_id):
                db.set_execution(steered_id, "completed", actor=f"{who.actor}:hook", source=source)
                implicit_reply(who, steered_id, payload.last_message)
                notify_change(steered_id)
        elif payload.event == "steered" and payload.request_id is not None:
            # A "now" message entered the running turn (RF-35): delivered and
            # being executed; the turn now answers more than one message.
            try:
                ack_request(who, payload.request_id, via="hook")
                db.set_execution(payload.request_id, "started", actor=f"{who.actor}:hook", source=source)
                db.mark_turn_mixed(payload.request_id)
                if session.turn_request_id is not None:
                    db.mark_turn_mixed(session.turn_request_id)
                notify_change(payload.request_id)
            except HTTPException as e:
                result["ack_error"] = e.detail
        elif payload.event == "tool" and payload.request_id is not None:
            result["linked"] = db.link_sender_session(payload.request_id, session_id, who.id)
        elif payload.event == "end":
            if session.turn_request_id is not None and session.state == "busy":
                db.set_execution(session.turn_request_id, "interrupted", actor=f"{who.actor}:hook",
                                 source=source)
                notify_change(session.turn_request_id)
            if ((payload.provider or session.provider) in SUSPENDING_PROVIDERS
                    or (session.provider == 'claude-code' and session.entrypoint == 'claude-desktop')):
                db.suspend_session(session_id)
            else:
                db.end_session(session_id)

        if payload.event_id:
            db.mark_session_event(payload.event_id, session_id)
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
                        envelopes.outcome(req, channel="hook", language=db.get_background_language()) if kind == "outcome"
                        else envelopes.injection(req, ack_mode=ack_mode, channel="hook", language=db.get_background_language())
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
        return db.list_sessions(agent_id=agent_id, include_ended=include_ended, include_excluded=False)

    @app.post("/api/sessions/{session_id}/messages", response_model=RequestModel,
              status_code=status.HTTP_201_CREATED)
    async def write_to_session(
        session_id: str, body: SessionMessage, who: Principal = Depends(principal)
    ):
        """
        The user writes to a session from the PWA (RF-34). No approval: the
        message is delivered like an approved prompt, through the session's
        channel, and the agent's answer is the turn it opens.
        """
        require_human(who)
        language = db.get_background_language()
        session = db.get_session(session_id)
        if session is None:
            raise ProductError(404, "session.not_found", "Session not found")
        if session.excluded:
            raise ProductError(409, "session.excluded", "This project is excluded from Placet (.placet-off).")
        if session.ended_at is not None:
            raise ProductError(409, "session.closed", "The session is closed.")
        if config.get_agent(session.agent_id) is None:
            raise ProductError(422, "session.unknown_agent", f"Unknown agent {session.agent_id}.")
        references = []
        mention_tokens = {}
        for mention in body.mentions:
            if mention.token in mention_tokens and mention_tokens[mention.token] != mention.session_id:
                raise ProductError(422, "mention.ambiguous", "Ambiguous mention: select the session again.")
            mention_tokens[mention.token] = mention.session_id
            target = db.get_session(mention.session_id)
            if mention.token not in body.text:
                raise ProductError(422, "mention.missing", "The mention is not present in the message.")
            if target is None or target.excluded or target.ended_at is not None:
                raise ProductError(409, "mention.unavailable", "A mentioned session is no longer available.")
            if target.id == session.id:
                raise ProductError(422, "mention.self", "You cannot mention the current session.")
            # A human's mention shares a reference, not a routed request.
            # Routing permissions and approval are checked when an agent sends.
            references.append({"mention": mention.token, "target_session": target.id,
                               "target_agent": target.agent_id, "title": target.title, "cwd": target.cwd})
        mention_context = None
        if references:
            mention_context = (
                text("message.references", language) + "\n"
                + json.dumps(references, ensure_ascii=False)
            )
        prompt = body.text.strip()
        if body.attachments:
            from bridge.attachments import AttachmentError
            from bridge.uploads import validate_uploads, save_uploads
            files = [a.model_dump() for a in body.attachments]
            try:
                validate_uploads(files)
            except AttachmentError as e:
                raise ProductError(413 if e.code == "too_large" else 415, "upload.invalid",
                                   "Invalid attachments: use JPEG, PNG, WebP or PDF within the stated limits.") from None
            saved = await bridge_call(
                "upload", {"session_id": session.id, "cwd": session.cwd, "files": files},
                lambda: save_uploads(session.id, files, cwd=session.cwd),
                {"excluded": (409, "Project excluded (.placet-off)."), "too_large": (413, "Attachment size or storage limit exceeded."),
                 "unsupported": (415, "Unsupported attachment format."), "forbidden": (403, "Unable to store attachments safely.")},
            )
            # Recheck after the transfer: the session may have ended or been excluded.
            current = db.get_session(session.id)
            if current is None or current.excluded or current.ended_at is not None:
                raise ProductError(409, "message.unavailable", "The session became unavailable: message not sent.")
            uploaded = saved.get("files") or []
            if len(uploaded) != len(files) or any(not f.get("path") for f in uploaded):
                raise ProductError(502, "upload.incomplete", "Attachment transfer was incomplete.")
            prompt = prompt or text("message.read_files", language)
            prompt += "\n\n" + text("message.attachments", language, names=", ".join(a.name for a in body.attachments))
            upload_context = (
                text("message.uploads", language) + "\n"
                + json.dumps(uploaded, ensure_ascii=False)
                + "\n" + text("message.upload_note", language)
            )
            mention_context = "\n\n".join(part for part in (mention_context, upload_context) if part)
        created = db.create_request(
            RequestCreate(
                sender_id="human", recipient_id=session.agent_id, question_type=QuestionType.MESSAGE,
                prompt=prompt, context=mention_context, recipient_session_id=session.id, priority=Priority.NORMAL,
            ),
            actor=who.actor,
        )
        if body.mode == "now":
            db.set_delivery_mode(created.id, "now")
            created = db.get_request(created.id)
        approved = db.update_request_decision(created.id, RequestStatus.APPROVED, actor=who.actor)
        notify_change(created.id)
        return approved

    @app.put("/api/sessions/{session_id}/pin", response_model=SessionModel)
    async def pin_session(session_id: str, body: PinPayload, who: Principal = Depends(principal)):
        """Adds a session to the PWA home or removes it (RF-41)."""
        require_human(who)
        if not db.set_session_pinned(session_id, body.pinned):
            raise ProductError(404, "session.not_found", "Session not found")
        notify_session(session_id)
        return db.get_session(session_id)

    async def bridge_call(kind: str, params: dict, local, errors: Dict[str, tuple]):
        """
        Work only the bridge can do (it runs as the user and reads the
        conversation files). In single-process development the backend runs
        as the user too and calls `local` directly.
        """
        def http_error(code):
            # Unknown diagnostics from the bridge are not product UI text.
            status_code, detail = errors.get(code or "", (502, "Bridge operation failed."))
            error_code = code if code in errors else "failed"
            return ProductError(status_code, f"{kind}.{error_code}", detail)

        if transcripts_local:
            from bridge.attachments import AttachmentError
            from bridge.transcripts import TranscriptError
            try:
                return await asyncio.to_thread(local)
            except (TranscriptError, AttachmentError) as e:
                raise http_error(e.code) from None
        try:
            result = await bridge_jobs.run(kind, params)
        except BridgeOffline:
            raise ProductError(503, "bridge.offline", "The bridge is offline.") from None
        except BridgeTimeout:
            raise ProductError(504, "bridge.timeout", "The bridge did not respond in time.") from None
        if not result.get("ok"):
            raise http_error(result.get("error"))
        return result.get("page") or {}

    @app.get("/api/sessions/{session_id}/transcript")
    async def session_transcript(
        session_id: str,
        before: Optional[int] = Query(None, ge=0),
        after: Optional[int] = Query(None, ge=0),
        limit: int = Query(40, ge=1, le=TRANSCRIPT_MAX_LIMIT),
        who: Principal = Depends(principal),
    ):
        """
        A page of a session's conversation, read by the bridge from the
        provider's files (RF-30, RF-31): the newest `limit` entries, older
        ones with `before`, new ones with `after` (the `end` of a page).
        """
        require_human(who)
        session = db.get_session(session_id)
        if session is None:
            raise ProductError(404, "session.not_found", "Session not found")
        if session.excluded:
            raise ProductError(409, "session.excluded", EXCLUDED_DETAIL)
        params = {
            "session_id": session.id, "provider": session.provider, "agent_id": session.agent_id,
            "before": before, "after": after, "limit": limit,
        }

        def local():
            from bridge.transcripts import read_transcript
            return read_transcript(**params)

        # The bridge checks the folder again: it may know of a .placet-off first.
        return await bridge_call("transcript", {**params, "cwd": session.cwd}, local, TRANSCRIPT_ERRORS)

    @app.get("/api/sessions/{session_id}/attachments/{attachment_id}")
    async def session_attachment(
        session_id: str,
        attachment_id: str = FastAPIPath(pattern=ATTACHMENT_ID_PATTERN),
        who: Principal = Depends(principal),
    ):
        """
        An image of a session's conversation, by the id its page gave it
        (never a path), read on demand through the bridge: no copy is kept.
        """
        require_human(who)
        session = db.get_session(session_id)
        if session is None:
            raise ProductError(404, "session.not_found", "Session not found")
        if session.excluded:
            raise ProductError(409, "session.excluded", EXCLUDED_DETAIL)
        params = {"session_id": session.id, "provider": session.provider, "agent_id": session.agent_id,
                  "attachment_id": attachment_id}

        def local():
            from bridge.transcripts import read_attachment
            mime, data = read_attachment(**params)
            return {"mime": mime, "data": base64.b64encode(data).decode("ascii")}

        result = await bridge_call("attachment", {**params, "cwd": session.cwd}, local, ATTACHMENT_ERRORS)
        mime = result.get("mime")
        if mime not in ("image/jpeg", "image/png", "image/webp", "application/pdf"):
            raise ProductError(415, "attachment.unsupported", ATTACHMENT_ERRORS["unsupported"][1])
        headers = {
            # Not kept by the browser or the service worker: the PC is the source.
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        }
        if mime != "application/pdf":
            headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
        else:
            # The browser's own PDF viewer; it does not run in the page's origin.
            headers["Content-Disposition"] = "inline"
        return Response(content=base64.b64decode(result.get("data") or ""), media_type=mime, headers=headers)

    # --- Local bridge (tmux injection and outcome delivery) -----------------

    @app.get("/api/bridge/jobs")
    async def bridge_take_jobs(
        wait_seconds: int = Query(0, ge=0, le=BRIDGE_JOBS_WAIT_SECONDS),
        who: Principal = Depends(principal),
    ):
        """Long-polled by the bridge: work someone is waiting for, or 204."""
        require_bridge(who)
        jobs = await bridge_jobs.take(wait_seconds)
        if not jobs:
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        return {"jobs": jobs}

    @app.post("/api/bridge/jobs/{job_id}/result")
    async def bridge_job_result(
        job_id: str, result: BridgeJobResult, who: Principal = Depends(principal)
    ):
        require_bridge(who)
        return {"accepted": bridge_jobs.resolve(job_id, result.model_dump())}

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
            "language": db.get_background_language(),
            "injections": injections,
            "results": results,
            "busy_agents": sorted(set(db.busy_agents(AGENT_BUSY_TTL_SECONDS)) | set(busy)),
            "claude_wake_sessions": [
                {"id": s.id, "cwd": s.cwd} for s in db.claude_sessions_to_wake()
            ],
            # Sessions the bridge wakes itself (Codex, Antigravity) with mail.
            "sessions": [
                {"id": s.id, "agent_id": s.agent_id, "provider": s.provider}
                for s in db.bridge_sessions_with_mail()
            ],
            "wake_sessions": [
                {"id": s.id, "provider": s.provider, "suspended": True}
                for s in db.bridge_sessions_with_mail(include_suspended=True)
                if s.suspended_at is not None
            ],
            # Uncertain deliveries: the bridge looks for them in the conversation.
            "uncertain": uncertain_to_reconcile(),
            # Folders of known sessions: the bridge looks for `.placet-off`.
            "folders": db.known_folders(),
            # "Now" messages for running turns, and those whose turn ended first.
            "steer": [{"request_id": r.id, "session_id": s.id, "provider": s.provider}
                      for r, s in db.steer_candidates()],
            "steer_waiting": [{"request_id": r.id, "session_id": s.id, "provider": s.provider}
                              for r, s in db.steers_waiting()],
            # Codex and Antigravity sessions: the bridge reads their titles.
            "titles": [{"id": s.id, "provider": s.provider} for s in db.untitled_bridge_sessions()],
        }

    @app.post("/api/bridge/requests/{request_id}/steer")
    async def bridge_steer(request_id: int, who: Principal = Depends(principal)):
        """Takes a "now" message for its session's running turn, with the text to insert."""
        require_bridge(who)
        req = get_or_404(request_id)
        session = db.get_session(req.recipient_session_id) if req.recipient_session_id else None
        if session is None or session.excluded or session.state != "busy" or req.delivery_mode != "now":
            raise HTTPException(status_code=409, detail="Not a running turn.")
        claimed = db.claim_steer(request_id, session.id, actor=who.actor)
        if claimed is None:
            raise HTTPException(status_code=409, detail=f"Request {request_id} is not APPROVED.")
        notify_change(request_id)
        return {"request_id": request_id, "session_id": session.id, "provider": session.provider,
                "text": envelopes.steer(claimed, language=db.get_background_language())}

    @app.post("/api/bridge/titles")
    async def bridge_titles(payload: TitlesReport, who: Principal = Depends(principal)):
        """Session titles the bridge read from the providers' apps."""
        require_bridge(who)
        changed = db.set_session_titles(payload.titles)
        for session_id in changed:
            notify_session(session_id)
        return {"changed": changed}

    @app.post("/api/bridge/folders")
    async def bridge_folders(payload: FoldersReport, who: Principal = Depends(principal)):
        """Which folders have a `.placet-off` (RF-45), as the bridge found them."""
        require_bridge(who)
        changed, cancelled = db.set_excluded_folders(payload.checked, payload.excluded, actor=who.actor)
        for session_id in changed:
            notify_session(session_id)
        for req_id in cancelled:
            notify_change(req_id)
        return {"changed": changed, "cancelled": cancelled}

    def uncertain_to_reconcile() -> List[Dict[str, object]]:
        out = []
        for req in db.uncertain_deliveries():
            session = db.get_session(req.recipient_session_id)
            if session is not None:
                out.append({"request_id": req.id, "session_id": session.id,
                            "agent_id": session.agent_id, "provider": session.provider})
        return out

    @app.post("/api/bridge/sessions/{session_id}/take")
    async def bridge_take_session_message(session_id: str, who: Principal = Depends(principal)):
        """
        Takes the next message for a session the bridge wakes (Codex,
        Antigravity), with the text to queue into its app; 204 if none.
        """
        require_bridge(who)
        session = db.get_session(session_id)
        if session is None or session.provider not in BRIDGE_PROVIDERS:
            raise HTTPException(status_code=404, detail="Session not found")
        taken = db.take_inbox_message(session, actor=who.actor, via="bridge")
        if taken is None:
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        kind, req = taken
        notify_change(req.id)
        text = envelopes.outcome(req, channel="app", language=db.get_background_language()) if kind == "outcome" else envelopes.injection(
            req, ack_mode="none", channel="app", language=db.get_background_language()
        )
        return {"kind": kind, "request_id": req.id, "text": text}

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

    @app.post("/api/bridge/requests/{request_id}/uncertain")
    async def bridge_uncertain(request_id: int, who: Principal = Depends(principal)):
        """The send ran but its outcome is unknown (e.g. a timeout): never re-sent by itself."""
        require_bridge(who)
        get_or_404(request_id)
        changed = db.mark_delivery_uncertain(request_id, actor=who.actor, reason="channel timeout")
        if changed:
            notify_change(request_id)
            alert(request_id, "delivery_uncertain")
        return {"id": request_id, "uncertain": changed}

    @app.post("/api/bridge/requests/{request_id}/seen", response_model=RequestModel)
    async def bridge_seen(request_id: int, who: Principal = Depends(principal)):
        """The message was found in the recipient's conversation: it was delivered."""
        require_bridge(who)
        req = get_or_404(request_id)
        if req.status != RequestStatus.DELIVERING:
            return req
        updated = db.confirm_injection(request_id, actor=who.actor, via="transcript")
        if updated is not None:
            notify_change(request_id)
        return updated or get_or_404(request_id)

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

    def request_excluded(req: RequestModel) -> bool:
        for session_id in (req.sender_session_id, req.recipient_session_id):
            session = db.get_session(session_id) if session_id else None
            if session is not None and session.excluded:
                return True
        return False

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
                    if req and not request_excluded(req):
                        yield {
                            "event": "request_update",
                            "data": req.model_dump_json(),
                        }
            except asyncio.CancelledError:
                sse_subscribers.remove(queue)

        return EventSourceResponse(event_generator())

    # Serve built PWA if frontend/dist exists
    dist_path = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
    mount_pwa(app, dist_path)

    return app



# Default app instance
app = create_app()
