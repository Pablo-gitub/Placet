from dataclasses import replace
import os
import time
import json
import asyncio
import base64
import contextlib
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, List, Literal, Optional, Dict, Set
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks, status, Request, Depends
from fastapi import Path as FastAPIPath
from fastapi.middleware.cors import CORSMiddleware

from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, model_validator
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
from backend.app.database import (
    ControlPending,
    Database, NameTaken, NodeNameTaken, ProjectNameTaken,
    compose_project_instructions,
)
from backend.app.session_commands import command_text, effort_levels
from backend.app.models import (
    ControlPayload,
    EffortPayload,
    NotificationSettings,
    ProviderUsage,
    UsageReport,
    RequestCreate,
    RequestEvent,
    RequestModel,
    RequestStatus,
    Priority,
    QuestionType,
    RequestDecision,
    OutcomeDecision,
    SessionMessage,
    RequestSchedule,
    NodeModel,
    ProjectModel,
    ProjectSummaryModel,
    ProjectDetailModel,
    RelationModel,
    RelationTypeModel,
    SessionModel,
    AGENT_PROVIDERS,
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
from backend.app import bridge_jobs as bridge_jobs_module
from backend.app.bridge_jobs import BridgeJobs, BridgeNotTaken, BridgeOffline, BridgeTimeout
from backend.app.webpush import WebPush, allowed_endpoint
from backend.app.admin_commands import AdminCommands, install_routes as install_admin_routes
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
    # The project root (main repository when in a worktree), for grouping and routing only.
    project_root: Optional[str] = Field(default=None, max_length=4096)
    model: Optional[str] = Field(default=None, max_length=100)
    title: Optional[str] = None
    tmux_session: Optional[str] = None
    # RF-62: the tmux pane id (%N) of the terminal the session runs in.
    tmux_pane: Optional[str] = Field(default=None, pattern=r"^%\d{1,9}$")
    # ...and who runs in it: the tmux server pid, the agent process pid and its start time.
    tmux_server: Optional[str] = Field(default=None, pattern=r"^\d{1,10}$")
    agent_pid: Optional[int] = Field(default=None, ge=2, le=2**32)
    agent_started: Optional[str] = Field(default=None, pattern=r"^\d{1,20}$")
    # prompt: the Placet request the prompt carries ("[Placet Request #N").
    # tool:   the request created by a Placet MCP call in this session.
    request_id: Optional[int] = None
    # stop: the agent's last message, used as implicit reply if needed.
    last_message: Optional[str] = None
    # start: why the session started (Claude Code: startup, resume, clear, compact).
    start_source: Optional[str] = Field(None, max_length=32)
    # RF-29d: UUID of the session being continued
    continues: Optional[str] = None
    # RF-27 (start): does the provider's configuration list the Placet MCP server?
    mcp: Optional[Literal["configured", "missing", "unknown"]] = None
    # RFC-16: the turn ended but the work goes on (stop), or this turn
    # continues it (prompt): an Antigravity background task or plan review.
    pause: Optional[Literal["background_task", "app_review"]] = None
    resumes: bool = False


class TitlesReport(BaseModel):
    titles: Dict[str, str] = Field(default_factory=dict, max_length=500)



# RF-62: an uncertain control this recent asks for an explicit confirmation before a repeat.
UNCERTAIN_CONFIRM_WINDOW = timedelta(minutes=30)

# RF-43: one "turn finished" notification per followed session at most this often.
TURN_END_NOTIFY_GAP = 60.0
# RF-09: a question to the user waits at least this long before returning PENDING.
MIN_HUMAN_WAIT_SECONDS = 60



class FoldersReport(BaseModel):
    checked: List[str] = Field(default_factory=list, max_length=1000)
    excluded: List[str] = Field(default_factory=list, max_length=1000)


EXCLUDED_DETAIL = "This project is excluded from Placet (.placet-off)."


def validate_scheduled_time(dt: Optional[datetime]) -> None:
    if dt is None:
        return
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    if dt < now - timedelta(minutes=1):
        raise ProductError(422, "schedule.time_in_past", "Scheduled time cannot be in the past.")
    if dt > now + timedelta(days=30):
        raise ProductError(422, "schedule.too_far_ahead", "Scheduled time cannot be more than 30 days in the future.")


class BackgroundLanguage(BaseModel):
    language: Literal["en", "it"]


class ClaudeRenewSetting(BaseModel):
    enabled: bool


class DeliverAtResetSetting(BaseModel):
    enabled: bool


class UsageGuardSetting(BaseModel):
    """Automatic relations fall back to review near a provider's usage limit."""
    enabled: bool
    five_hour: int = Field(ge=50, le=100)
    seven_day: int = Field(ge=50, le=100)
    deliver_at_reset: Optional[bool] = None


class QueueRelease(BaseModel):
    reason: Literal["finished", "queue_next"]


class PinPayload(BaseModel):
    pinned: bool


# RFC-02: names agents and people use to address a node.
NODE_NAME = r"^[\w][\w .\-]{0,39}$"


class NodeCreate(BaseModel):
    name: str = Field(..., pattern=NODE_NAME)
    session_id: str = Field(..., min_length=1, max_length=200)


class NodeRename(BaseModel):
    name: str = Field(..., pattern=NODE_NAME)


class NodeAttach(BaseModel):
    node_id: Optional[int] = None


class RelationTypeCreate(BaseModel):
    name: str = Field(..., pattern=NODE_NAME)
    description: str = Field("", max_length=500)


class RelationTypeUpdate(BaseModel):
    name: Optional[str] = Field(None, pattern=NODE_NAME)
    description: Optional[str] = Field(None, max_length=500)


class ProgressPayload(BaseModel):
    content: str = Field(min_length=1, max_length=2000)


class RelationCreate(BaseModel):
    from_node: int
    to_node: int
    type_id: int
    approval: Literal["manual", "automatic"] = "manual"
    # RFC-16: defaults to the outbound policy.
    reply_approval: Optional[Literal["manual", "automatic"]] = None


class RelationUpdate(BaseModel):
    approval: Optional[Literal["manual", "automatic"]] = None
    reply_approval: Optional[Literal["manual", "automatic"]] = None

    @model_validator(mode="after")
    def _one_policy_given(self):
        if self.approval is None and self.reply_approval is None:
            raise ValueError("approval or reply_approval is required")
        return self


class AutomationLimits(BaseModel):
    max_hops: Optional[int] = Field(None, ge=1, le=10)
    max_per_hour: Optional[int] = Field(None, ge=1, le=200)


class ProjectCreate(BaseModel):
    name: str = Field(..., pattern=NODE_NAME)
    description: str = Field("", max_length=500)
    relation_rule: Optional[str] = Field(None, max_length=4000)
    max_auto_hops: Optional[int] = Field(None, ge=1, le=100)
    max_auto_per_hour: Optional[int] = Field(None, ge=1, le=1000)
    instructions: Optional[str] = Field(None, max_length=4000)


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(None, pattern=NODE_NAME)
    description: Optional[str] = Field(None, max_length=500)
    relation_rule: Optional[str] = Field(None, max_length=4000)
    max_auto_hops: Optional[int] = Field(None, ge=1, le=100)
    max_auto_per_hour: Optional[int] = Field(None, ge=1, le=1000)
    instructions: Optional[str] = Field(None, max_length=4000)


class ProjectNodeUpdate(BaseModel):
    instructions: Optional[str] = Field(None, max_length=4000)


class PushKeys(BaseModel):
    p256dh: str = Field(..., min_length=1, max_length=200)
    auth: str = Field(..., min_length=1, max_length=100)


class PushSubscription(BaseModel):
    endpoint: str = Field(..., min_length=1, max_length=1000)
    keys: Optional[PushKeys] = None


class RelationRule(BaseModel):
    rule: str = Field("", max_length=4000)


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
    web_push: Optional[WebPush] = None,
    admin_commands=None,
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

    def stored_usage() -> List["ProviderUsage"]:
        return [ProviderUsage.model_validate_json(d) for d in db.list_usage()]

    def notify_session(session_id: str):
        for ev in list(inbox_waiters):
            ev.set()
        _broadcast(("session", session_id))

    background_alerts: Set[asyncio.Task] = set()

    def web_push_later(title: str, body: str, url: str, tag: str, urgency: str = "normal") -> None:
        """Web Push to the installed PWA: the tap opens it on `url` (same origin)."""
        if web_push is None:
            return
        try:
            task = asyncio.get_running_loop().create_task(web_push.send(title, body, url, tag, urgency))
        except RuntimeError:
            return
        background_alerts.add(task)
        task.add_done_callback(background_alerts.discard)

    def alert(req_id: int, problem: str):
        prefs = db.get_notification_preferences()
        if problem == "return_waiting":
            if not prefs.get("needs_you", True):
                return
        else:
            if not prefs.get("problems", True):
                return
        req = db.get_request(req_id)
        if req is None:
            return
        try:
            task = asyncio.get_running_loop().create_task(send_ntfy_alert(req, replace(ntfy, language=db.get_background_language()), text(f"alerts.{problem}", db.get_background_language())))
        except RuntimeError:
            return  # no running loop (sync caller): the PWA still shows it
        background_alerts.add(task)
        task.add_done_callback(background_alerts.discard)
        lang = db.get_background_language()
        web_push_later(f"Placet #{req.id}: {text(f'alerts.{problem}', lang)}",
                       f"{req.sender_id} -> {req.recipient_id}", f"/#request-{req.id}", f"alert-{req.id}", "high")

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
        for req_id in db.long_pauses():
            notify_change(req_id)
            alert(req_id, "paused_long")
        # Claude Desktop wake-up: windows that ended without evidence, episodes that are over.
        swept = db.settle_wake_episodes()
        for session_id in swept["unconfirmed"] + swept["ended"]:
            notify_session(session_id)
        for session_id in db.wake_alert_candidates():
            alert_wake(session_id)
        # Switched off, nothing is marked: turning it on later still alerts.
        if db.get_notification_preferences().get("queue_held", True):
            for req_id, session_id, waiting in db.held_queues_to_alert():
                alert_held_queue(req_id, session_id, waiting)
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

    def alert_wake(session_id: str) -> None:
        """
        A Claude Desktop session was asked to open and never started listening (or the link never
        reached the app): one needs-you notification per episode, never repeated, never when the
        user turned that kind off (and then not consumed).
        """
        if not db.get_notification_preferences().get("needs_you", True):
            return
        session = db.get_session(session_id)
        if session is None or not db.claim_wake_alert(session_id):
            return
        lang = db.get_background_language()
        key = "wake_failed" if session.wake_state == "wake_failed" else "wake_unconfirmed"
        web_push_later(text(f"notify.{key}_title", lang,
                            label=session_label(session.provider, session.title, session.project_root or session.cwd)),
                       text(f"notify.{key}_body", lang), f"/#/sessione/{session_id}", f"wake-{session_id}", "high")

    def alert_held_queue(req_id: int, session_id: str, waiting: int) -> None:
        session = db.get_session(session_id)
        if session is None:
            return
        lang = db.get_background_language()
        key = "notify.queue_held_title_one" if waiting == 1 else "notify.queue_held_title"
        web_push_later(text(key, lang, label=session_label(session.provider, session.title, session.project_root or session.cwd),
                            count=waiting),
                       text("notify.queue_held_body", lang), f"/#/sessione/{session_id}",
                       f"queue-held-{req_id}", "high")

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
    # RF-62: a control left pending belongs to a backend that is gone: its outcome is unknown.
    # Only here (the backend starting), not in init_db, which the bridge's own store also runs.
    db.settle_pending_session_controls()

    if config is None:
        config = load_agents_config()

    if ntfy is None:
        ntfy = NtfyConfig.from_env()

    if web_push is None:
        web_push = WebPush(db, subject=os.environ.get("PLACET_BASE_URL", "").rstrip("/"))

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

    install_admin_routes(app, principal, AdminCommands() if admin_commands is None else admin_commands)

    def get_or_404(request_id: int) -> RequestModel:
        req = db.get_request(request_id)
        if not req:
            raise HTTPException(status_code=404, detail="Request not found")
        return req

    def relation_of(sender_session_id: Optional[str], recipient_session_id: Optional[str],
                    type_id: Optional[int] = None):
        """The relation between the nodes of two sessions, if there is one (RFC-04)."""
        if not sender_session_id or not recipient_session_id:
            return None
        sender = db.get_session(sender_session_id)
        recipient = db.get_session(recipient_session_id)
        if sender is None or recipient is None:
            return None
        return db.find_relation(sender.node_id, recipient.node_id, type_id)

    def relation_brief(relation) -> Dict[str, object]:
        return {"id": relation.id, "from": relation.from_name, "to": relation.to_name,
                "type": relation.type_name, "description": relation.type_description,
                "approval": relation.approval}

    def reply_is_automatic(relation) -> bool:
        """RFC-16: the relation's own reply policy, held back by the usage guard."""
        if relation is None:
            return False
        cfg = usage_guard()
        if not cfg.get("deliver_at_reset", False) and relation_guarded(relation):
            return False
        return relation.reply_approval == "automatic"

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
        return session_label(session.provider, session.title, session.project_root or session.cwd) or None

    # A followed session's turn ends: at most one quiet notification a minute each.
    turn_end_notified: Dict[str, float] = {}

    def paused_request(session: SessionModel) -> Optional[int]:
        """The request a busy session's turn belongs to, while it is still running."""
        if session.state != "busy" or session.turn_request_id is None:
            return None
        req = db.get_request(session.turn_request_id)
        return req.id if req is not None and req.status == RequestStatus.EXECUTING else None

    def notify_app_review(session: SessionModel) -> None:
        """The agent waits for the user's approval inside its own app (RFC-16)."""
        if not db.get_notification_preferences().get("needs_you", True):
            return
        lang = db.get_background_language()
        web_push_later(text("notify.app_review_title", lang,
                            label=session_label(session.provider, session.title, session.project_root or session.cwd)),
                       text("notify.app_review_body", lang), f"/#/sessione/{session.id}",
                       f"app-review-{session.id}", "high")

    def notify_turn_end(session: SessionModel, last_message: Optional[str], is_placet: bool = False) -> None:
        if not session.pinned or session.excluded:
            return
        prefs = db.get_notification_preferences()
        mode = prefs.get("turn_end", "placet")
        if mode == "off":
            return
        if mode == "placet" and not is_placet:
            return
        loop_time = asyncio.get_running_loop().time()
        if loop_time - turn_end_notified.get(session.id, -TURN_END_NOTIFY_GAP) < TURN_END_NOTIFY_GAP:
            return
        turn_end_notified[session.id] = loop_time
        task = asyncio.get_running_loop().create_task(send_ntfy_turn_end(
            session.id, session_label(session.provider, session.title, session.project_root or session.cwd), last_message, replace(ntfy, language=db.get_background_language())))
        background_alerts.add(task)
        task.add_done_callback(background_alerts.discard)
        lang = db.get_background_language()
        web_push_later(text("notify.turn_title", lang, label=session_label(session.provider, session.title, session.project_root or session.cwd)),
                       text("notify.turn_body", lang), f"/#/sessione/{session.id}", f"turn-{session.id}", "low")

    def bind_unaddressed_session(req: RequestCreate, sender_session: Optional[str]) -> RequestCreate:
        """
        A request for an agent, without a session or node, is bound at creation
        to a session of that agent in the sender's folder (any folder, when the
        sender is unknown), never left for the first idle session, which may be
        stale or foreign: the one reachable session there; else the one known
        session there that cannot be woken now (suspended, waiter gone), which
        gets the request when it comes back and nobody else can claim it.
        Several, or none there while other sessions exist: refused with the
        sessions. An agent with no session at all, or only tmux-driven ones the
        bridge reaches, keeps the old behaviour.
        """
        sender = db.get_session(sender_session) if sender_session else None
        # Compare project roots (the main repository when working in a worktree), real paths
        # on both sides; a session that reports none is compared by its working directory.
        folder_of = lambda s: os.path.realpath(s.project_root or s.cwd) if (s.project_root or s.cwd) else None  # noqa: E731
        folder = folder_of(sender) if sender is not None else None
        # The sender is never a candidate: a session cannot receive its own request.
        others = [s for s in db.list_sessions(agent_id=req.recipient_id)
                  if s.id != sender_session and not s.excluded]
        reachable = [s for s in others if s.reachable]
        waiting = [s for s in others if not s.reachable and not s.tmux_session]
        here = lambda pool: [s for s in pool if folder is None or folder_of(s) == folder]  # noqa: E731
        brief = lambda s: {"id": s.id, "title": s.title, "cwd": s.cwd, "project_root": s.project_root}  # noqa: E731
        for pool in (here(reachable), here(waiting) if not here(reachable) else []):
            if len(pool) == 1:
                return req.model_copy(update={"recipient_session_id": pool[0].id})
        if not reachable and not waiting:
            return req
        candidates = here(reachable) or here(waiting)
        where = f" in {folder}" if folder else ""
        ambiguous = bool(reachable)
        raise ProductError(
            409, "request.target_session_ambiguous" if ambiguous or candidates else "request.target_session_unreachable",
            (f"{len(candidates)} {req.recipient_id} sessions{where}: " if candidates
             else f"No reachable {req.recipient_id} session{where}. ")
            + "Pass target_session (an ID from placet_list_agents), or ask the human which one. Nothing was sent.",
            {"candidates": [brief(s) for s in candidates],
             "other_sessions": [brief(s) for s in reachable + waiting if s not in candidates]},
        )

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

    @app.get("/api/settings/claude-renew", response_model=ClaudeRenewSetting)
    async def get_claude_renew_setting(response: Response, who: Principal = Depends(principal)):
        require_human(who)
        response.headers["Cache-Control"] = "no-store"
        return {"enabled": db.get_setting("claude_auto_renew") == "true"}

    @app.put("/api/settings/claude-renew", response_model=ClaudeRenewSetting)
    async def set_claude_renew_setting(payload: ClaudeRenewSetting, response: Response, who: Principal = Depends(principal)):
        require_human(who)
        db.set_setting("claude_auto_renew", "true" if payload.enabled else "false")
        response.headers["Cache-Control"] = "no-store"
        return {"enabled": db.get_setting("claude_auto_renew") == "true"}

    @app.get("/api/settings/notifications", response_model=NotificationSettings)
    async def get_notification_settings(response: Response, who: Principal = Depends(principal)):
        require_human(who)
        response.headers["Cache-Control"] = "no-store"
        return db.get_notification_preferences()

    @app.put("/api/settings/notifications", response_model=NotificationSettings)
    async def set_notification_settings(payload: NotificationSettings, response: Response, who: Principal = Depends(principal)):
        require_human(who)
        db.set_notification_preferences(payload.model_dump())
        response.headers["Cache-Control"] = "no-store"
        return db.get_notification_preferences()

    def usage_guard() -> Dict[str, Any]:
        return db.usage_guard_config()

    def usage_guarded() -> Set[str]:
        """The providers whose automatic relations are reviewed now, if the guard is on."""
        cfg = usage_guard()
        if not cfg["enabled"]:
            return set()
        return db.near_limit_providers(cfg["five_hour"], cfg["seven_day"])

    def relation_guarded(relation) -> Optional[str]:
        """The provider at either end of a relation that is near its usage limit."""
        guarded = usage_guarded()
        if relation is None or not guarded:
            return None
        for node_id in (relation.from_node, relation.to_node):
            node = db.get_node(node_id)
            session = db.get_session(node.session_id) if node and node.session_id else None
            if session is not None and session.provider in guarded:
                return session.provider
        return None

    @app.get("/api/settings/usage-guard")
    async def get_usage_guard(response: Response, who: Principal = Depends(principal)):
        require_human(who)
        response.headers["Cache-Control"] = "no-store"
        cfg = usage_guard()
        return {"enabled": cfg["enabled"], "five_hour": cfg["five_hour"], "seven_day": cfg["seven_day"], "guarded": sorted(usage_guarded())}

    @app.put("/api/settings/usage-guard")
    async def set_usage_guard(payload: UsageGuardSetting, response: Response, who: Principal = Depends(principal)):
        require_human(who)
        db.set_setting("usage_guard_enabled", "true" if payload.enabled else "false")
        db.set_setting("usage_guard_5h", str(payload.five_hour))
        db.set_setting("usage_guard_7d", str(payload.seven_day))
        if payload.deliver_at_reset is not None:
            val = "true" if payload.deliver_at_reset else "false"
            db.set_setting("deliver_at_reset", val)
        response.headers["Cache-Control"] = "no-store"
        cfg = usage_guard()
        return {"enabled": cfg["enabled"], "five_hour": cfg["five_hour"], "seven_day": cfg["seven_day"], "guarded": sorted(usage_guarded())}

    @app.get("/api/settings/deliver-at-reset", response_model=DeliverAtResetSetting)
    async def get_deliver_at_reset_setting(response: Response, who: Principal = Depends(principal)):
        require_human(who)
        response.headers["Cache-Control"] = "no-store"
        return {"enabled": db.get_setting("deliver_at_reset") == "true"}

    @app.put("/api/settings/deliver-at-reset", response_model=DeliverAtResetSetting)
    async def set_deliver_at_reset_setting(payload: DeliverAtResetSetting, response: Response, who: Principal = Depends(principal)):
        require_human(who)
        val = "true" if payload.enabled else "false"
        db.set_setting("deliver_at_reset", val)
        response.headers["Cache-Control"] = "no-store"
        return {"enabled": payload.enabled}

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

        if req.scheduled_at is not None:
            if not who.is_human:
                raise ProductError(422, "schedule.not_allowed", "Only human users can schedule requests.")
            validate_scheduled_time(req.scheduled_at)

        # RFC-08: a node by name stands for its current session and agent.
        if req.target_node:
            node = db.node_by_name(req.target_node.strip())
            target = db.get_session(node.session_id) if node and node.session_id else None
            if target is None:
                raise ProductError(404, "node.not_found", f"No node named {req.target_node!r} with a session.")
            if req.recipient_id not in (None, target.agent_id):
                raise ProductError(422, "node.other_agent", f"Node {node.name!r} belongs to {target.agent_id}.")
            req = req.model_copy(update={"recipient_id": target.agent_id, "recipient_session_id": target.id})
        if req.relation_type:
            rtype = db.relation_type_by_name(req.relation_type.strip())
            if rtype is None:
                raise ProductError(404, "relation_type.not_found", f"No relation type named {req.relation_type!r}.")
            req = req.model_copy(update={"relation_type_id": rtype.id})
        if not req.recipient_id:
            raise ProductError(422, "request.no_recipient", "Specify recipient_id or target_node.")

        # The relation is known before anything is created (RFC-04/06).
        if req.sender_session_id:
            own = db.get_session(req.sender_session_id)
            if own is None or own.agent_id != who.id:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                    detail="sender_session_id is not a session of this agent.")
            sender_session = own.id
        else:
            sender_session = created_sender_session(who)
        if (req.recipient_id != "human" and req.recipient_session_id is None
                and config.get_agent(req.recipient_id) is not None):
            req = bind_unaddressed_session(req, sender_session)
        relation = (relation_of(sender_session, req.recipient_session_id, req.relation_type_id)
                    if req.recipient_id != "human" else None)
        if req.target_node and relation is None:
            # Addressing a node means using a relation: never fall back to an
            # ad hoc request the sender did not ask for.
            if sender_session is None:
                raise ProductError(409, "relation.sender_unknown",
                                   "Placet cannot tell which session is sending: pass sender_session_id "
                                   "(your session ID). Nothing was sent.")
            raise ProductError(409, "relation.not_found",
                               f"No relation from your node to {req.target_node!r}"
                               + (f" of type {req.relation_type!r}" if req.relation_type else "")
                               + ". Nothing was sent; see placet_list_relations.")
        if req.recipient_id != "human" and sender_session is not None:
            # RFC-16: while this session runs a request from the recipient,
            # its answer is that request's reply, not a new request.
            running = db.open_request_toward(sender_session, req.recipient_id, req.recipient_session_id)
            if running is not None:
                raise ProductError(409, "request.reply_expected",
                                   f"You are running request #{running} from this recipient: answer it with "
                                   f"placet_reply(request_id={running}); send progress with placet_progress. "
                                   "Ask the human with placet_ask_human if you need a decision. Nothing was sent.",
                                   {"request_id": running})
        rule = (db.get_relation_effective_policy(relation.id)["rule"]
                if relation is not None else db.get_relation_rule().strip())
        if relation is not None and relation.approval == "automatic" and rule and not req.rule_acknowledged:
            # Read every time, right before an automatic relation is used.
            return JSONResponse(status_code=428, content={
                "status": "RULE_ACKNOWLEDGEMENT_REQUIRED",
                "code": "relation.rule_ack_required",
                "relation": relation_brief(relation),
                "rule": rule,
                "detail": "Nothing was sent. Read the rule; if it asks you to stop or ask the human "
                          "first, do that. Otherwise send again with rule_acknowledged=true.",
            })

        # Validate allowed route: AGENTS.md, or a relation the user created.
        if relation is None and not config.is_route_allowed(req.sender_id, req.recipient_id):
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
            if req.recipient_session_id == sender_session:
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
        if sender_session and db.link_sender_session(created.id, sender_session, who.id):
            created = db.get_request(created.id)
            db.mark_mcp_seen(sender_session)  # RF-27: a Placet MCP call came from this session

        # RFC-04/05: the relation between the two nodes decides the policy.
        by_relation = False
        effective_policy = None
        if req.recipient_id != "human" and relation is None:
            relation = relation_of(created.sender_session_id, created.recipient_session_id,
                                   req.relation_type_id)
        if relation is not None:
            db.set_request_relation(created.id, relation.id)
            # RFC-15: project instructions for relation
            projects_for_rel = db.list_projects_for_relation(relation.id)
            blocks = []
            for proj in projects_for_rel:
                node_inst = db.get_project_node_instructions(proj.id, relation.to_node)
                outgoing = db.list_node_project_outgoing_relations(proj.id, relation.to_node)
                b = compose_project_instructions(proj.name, proj.instructions, node_inst, outgoing)
                if b:
                    blocks.append(b)
            if blocks:
                db.set_request_project_context(created.id, "\n\n".join(blocks))
            created = db.get_request(created.id)
            effective_policy = db.get_relation_effective_policy(relation.id)
            effective_rule = effective_policy["rule"]
            # Never automatic without the rule read (a relation found only now).
            if relation.approval == "automatic" and (req.rule_acknowledged or not effective_rule):
                needs_approval = False
                by_relation = True

        # RFC-14: loop limits turn an automatic relation back into a review.
        parent_id, hops_before = db.chain_start(created.sender_session_id)
        automation_limit = None
        if by_relation and effective_policy:
            limits = effective_policy
            if limits["paused"]:
                automation_limit = "paused"
            elif hops_before + 1 > limits["max_hops"]:
                automation_limit = "hops"
            elif db.automatic_sends_last_hour(relation.id) >= limits["max_per_hour"]:
                automation_limit = "rate"
            details: Dict[str, object] = {}
            cfg = usage_guard()
            deliver_at_reset_on = cfg.get("deliver_at_reset", False)

            to_node = db.get_node(relation.to_node) if relation.to_node else None
            rec_session = db.get_session(to_node.session_id) if to_node and to_node.session_id else None
            rec_provider = rec_session.provider if rec_session else None
            rec_over = rec_provider and db.is_provider_over_limit(
                rec_provider, cfg["five_hour"], cfg["seven_day"], check_near_limit=cfg["enabled"]
            )

            from_node = db.get_node(relation.from_node) if relation.from_node else None
            snd_session = db.get_session(from_node.session_id) if from_node and from_node.session_id else None
            snd_provider = snd_session.provider if snd_session else None
            snd_over = snd_provider and db.is_provider_over_limit(
                snd_provider, cfg["five_hour"], cfg["seven_day"], check_near_limit=cfg["enabled"]
            )

            if not automation_limit:
                if rec_over:
                    if deliver_at_reset_on:
                        rec_reset = db.provider_reset_time(
                            rec_provider, cfg["five_hour"], cfg["seven_day"], check_near_limit=cfg["enabled"]
                        )
                        if rec_reset is not None:
                            db.schedule_request(created.id, rec_reset, actor=f"system:relation#{relation.id}")
                            created = db.get_request(created.id)
                        else:
                            automation_limit, details = "usage", {"provider": rec_provider}
                    else:
                        automation_limit, details = "usage", {"provider": rec_provider}
                elif snd_over:
                    if not deliver_at_reset_on:
                        automation_limit, details = "usage", {"provider": snd_provider}
            if automation_limit:
                needs_approval, by_relation = True, False
                db.log_event(created.id, "system:limits", "automation_limited",
                             {"reason": automation_limit, "hops": hops_before + 1, **limits, **details})
        db.set_chain(created.id, parent_id, hops_before + 1 if by_relation else 0)
        created = db.get_request(created.id)

        if not needs_approval:
            # Auto-approval: AGENTS.md routing, or an automatic relation.
            created = db.update_request_decision(
                request_id=created.id,
                status=RequestStatus.APPROVED,
                actor=f"system:relation#{relation.id}" if by_relation else "system:auto-routing",
                allow_auto_return=reply_is_automatic(relation),
            )
        else:
            # Send mobile notification
            prefs = db.get_notification_preferences()
            if prefs.get("needs_you", True):
                background_tasks.add_task(
                    send_ntfy_notification,
                    request=created,
                    cfg=replace(ntfy, language=db.get_background_language()),
                    origin=origin_of(created),
                )
                lang = db.get_background_language()
                sender = origin_of(created) or created.sender_id
                web_push_later(
                    text("notify.question", lang, sender=sender) if created.recipient_id == "human"
                    else text("notify.review", lang, sender=sender, recipient=created.recipient_id),
                    text("notify.public", lang, sender=created.sender_id),
                    f"/#request-{created.id}", f"request-{created.id}",
                    "high" if created.priority in (Priority.HIGH, Priority.URGENT) else "normal")
        notify_change(created.id)

        extra: Dict[str, object] = {}
        if req.recipient_id != "human":
            # RFC-06: the sender knows whether the user reviews its prompt.
            extra["delivery_policy"] = "automatic" if not needs_approval else "manual"
            if automation_limit:
                extra["automation_limit"] = automation_limit
                extra["note"] = text(f"envelope.limit_{automation_limit}", db.get_background_language())
            if relation is not None:
                extra["relation"] = relation_brief(relation)
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

    def mark_mcp_seen_by(who: Principal, req: RequestModel) -> None:
        """
        RF-27: an agent reading a request (placet_check_status) used the MCP; the session it
        did so from is its own end of the request. Neither a human, nor an agent that is both
        ends (which of its sessions is unknown).
        """
        if who.is_human:
            return
        if who.id == req.sender_id and who.id != req.recipient_id:
            db.mark_mcp_seen(req.sender_session_id)
        elif who.id == req.recipient_id and who.id != req.sender_id:
            db.mark_mcp_seen(req.recipient_session_id)

    @app.get("/api/requests/{request_id}", response_model=RequestModel)
    async def get_request(
        request_id: int,
        wait_seconds: int = Query(0, ge=0, le=600),
        who: Principal = Depends(principal),
    ):
        found = get_or_404(request_id)
        require_party(who, found)
        mark_mcp_seen_by(who, found)
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
        events = db.list_events(request_id)
        if not who.is_human:
            # Audit may contain a human draft even when forwarding was rejected.
            # Agents get outcome metadata; authorized reply text uses the reply path.
            events = [event.model_copy(update={"details": {
                key: value for key, value in event.details.items() if key != "edited_response"
            }}) if event.event == "return_decided" and event.details else event for event in events]
        return events

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
        relation = db.get_relation(req.relation_id) if req.relation_id else None
        redirect = None
        if decision.recipient_session_id is not None:
            target = db.get_session(decision.recipient_session_id)
            # The user may send an agent's prompt to a session of another agent.
            redirect_to = (target.agent_id if target is not None and req.recipient_id != "human"
                           and req.question_type == QuestionType.PROMPT_REVIEW
                           and decision.status == RequestStatus.APPROVED
                           and target.agent_id != req.recipient_id else None)
            check_target_session(decision.recipient_session_id, redirect_to or req.recipient_id)
            if decision.recipient_session_id == req.sender_session_id:
                raise ProductError(422, "decision.self_target", "A session cannot send a request to itself.")
            if req.recipient_id != "human" and decision.recipient_session_id != req.recipient_session_id:
                # Another recipient: the relation the request came on may no longer apply.
                relation = relation_of(req.sender_session_id, decision.recipient_session_id)
                redirect = (redirect_to, relation.id if relation else None)
        if decision.status == RequestStatus.APPROVED and decision.delivery_mode == "now":
            # Only a delivery still ahead conflicts with "now": a schedule that has passed is just due.
            if req.scheduled_at is not None and req.scheduled_at.astimezone(timezone.utc) > datetime.now(timezone.utc):
                raise ProductError(422, "schedule.steer_conflict", "A message in 'now' mode cannot be scheduled.")
            target_id = decision.recipient_session_id or req.recipient_session_id
            target = db.get_session(target_id) if target_id else None
            if (req.question_type != QuestionType.PROMPT_REVIEW or req.recipient_id == "human"
                    or target is None or target.provider not in STEER_PROVIDERS):
                raise ProductError(422, "decision.now_target", "Now requires a specific Claude or Antigravity session.")
            check_target_session(target_id, (redirect and redirect[0]) or req.recipient_id)

        if redirect is not None and not db.redirect_request(
                request_id, redirect[0], redirect[1], decision.recipient_session_id, who.actor):
            raise ProductError(409, "decision.not_pending",
                               f"Request {request_id} is no longer in PENDING state.", {"id": request_id})

        # RF-66: If approved message to an over-limit recipient and deliver_at_reset is ON:
        # schedule delivery at recipient's reset time (unless mode is "now").
        effective_schedule = None
        if decision.status == RequestStatus.APPROVED and decision.delivery_mode != "now" and req.recipient_id != "human":
            target_id = decision.recipient_session_id or req.recipient_session_id
            target_sess = db.get_session(target_id) if target_id else None
            if target_sess is None and relation and relation.to_node:
                node = db.get_node(relation.to_node)
                target_sess = db.get_session(node.session_id) if node and node.session_id else None
            rec_provider = target_sess.provider if target_sess else None
            if rec_provider is None:
                rec_provider = AGENT_PROVIDERS.get(req.recipient_id, req.recipient_id)

            cfg = usage_guard()
            if cfg.get("deliver_at_reset", False) and rec_provider:
                if db.is_provider_over_limit(rec_provider, cfg["five_hour"], cfg["seven_day"], check_near_limit=cfg["enabled"]):
                    rec_reset = db.provider_reset_time(rec_provider, cfg["five_hour"], cfg["seven_day"], check_near_limit=cfg["enabled"])
                    if rec_reset is not None:
                        if req.scheduled_at is not None:
                            effective_schedule = max(req.scheduled_at, rec_reset)
                        else:
                            effective_schedule = rec_reset

        updated = db.update_request_decision(
            request_id=request_id,
            status=decision.status,
            edited_prompt=decision.edited_prompt,
            selected_options=decision.selected_options,
            custom_text=decision.custom_text,
            actor=who.actor,
            allow_auto_return=decision.allow_auto_return or reply_is_automatic(relation),
            delivery_mode=decision.delivery_mode if decision.status == RequestStatus.APPROVED and req.recipient_id != "human" else None,
            recipient_session_id=decision.recipient_session_id,
            scheduled_at=effective_schedule,
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

    @app.post("/api/requests/{request_id}/schedule", response_model=RequestModel)
    async def schedule_request(
        request_id: int, body: RequestSchedule, who: Principal = Depends(principal)
    ):
        """
        Schedules or clears the scheduled delivery time of a request (RF-65).
        Allowed only for the human supervisor, while the request is PENDING or APPROVED
        with delivery waiting.
        """
        require_human(who)
        validate_scheduled_time(body.scheduled_at)
        req = db.get_request(request_id)
        if req is None:
            raise ProductError(404, "request.not_found", "Request not found")
        if (
            req.status not in (RequestStatus.PENDING, RequestStatus.APPROVED, RequestStatus.COMPLETED)
            or req.delivered_at is not None
            or (req.delivery != "waiting" and req.status != RequestStatus.COMPLETED)
        ):
            raise ProductError(409, "schedule.not_allowed", "This request can no longer be scheduled.")
        updated = db.schedule_request(request_id, body.scheduled_at, actor=who.actor)
        if updated is None:
            raise ProductError(409, "schedule.not_allowed", "This request can no longer be scheduled.")
        notify_change(request_id)
        return updated

    def forward_reply(req: RequestModel, payload: OutcomeDecision, who: Principal) -> RequestModel:
        """
        The user sends an agent's reply to another session instead of the
        sender: a new prompt from the replying agent, already approved; the
        sender gets only a notice (RFC-07b).
        """
        target = db.get_session(payload.forward_session_id)
        if not payload.approve or target is None or req.response is None:
            raise ProductError(422, "return.forward_invalid", "Choose an open session to forward the reply to.")
        if target.id in (req.sender_session_id, req.recipient_session_id):
            raise ProductError(422, "return.forward_invalid", "Choose a session other than the sender and the one that replied.")
        check_target_session(target.id, target.agent_id)
        content = payload.edited_response if payload.edited_response is not None else req.response.content
        if not db.claim_return_forward(req.id, content, who.actor):
            raise ProductError(409, "return.unavailable", "The reply has already been decided, forwarded, or is no longer available.")
        forward = db.create_request(RequestCreate(
            sender_id=req.recipient_id, recipient_id=target.agent_id, prompt=content,
            recipient_session_id=target.id, priority=Priority.NORMAL), actor=who.actor)
        if req.recipient_session_id:
            db.link_sender_session(forward.id, req.recipient_session_id, req.recipient_id)
        db.update_request_decision(forward.id, RequestStatus.APPROVED, actor=who.actor)
        db.set_return_forward(req.id, forward.id, who.actor)
        notify_change(forward.id)
        notify_change(req.id)
        return db.get_request(req.id)

    @app.post("/api/requests/{request_id}/return-decision", response_model=RequestModel)
    async def decide_return(request_id: int, payload: OutcomeDecision,
                            who: Principal = Depends(principal)):
        require_human(who)
        req = get_or_404(request_id)
        if payload.edited_response is not None and not payload.edited_response.strip():
            raise ProductError(422, "return.empty", "The reply cannot be empty.")
        if payload.forward_session_id is not None:
            return forward_reply(req, payload, who)
        updated = db.decide_return(request_id, payload.approve, payload.edited_response, who.actor)
        if updated is None:
            raise ProductError(409, "return.unavailable", "The reply has already been decided, forwarded, or is no longer available.")
        notify_change(request_id)
        return updated

    @app.post("/api/requests/{request_id}/progress", response_model=RequestModel)
    async def progress_request(request_id: int, payload: ProgressPayload, who: Principal = Depends(principal)):
        """RFC-16: a progress update from the recipient; the request stays open."""
        require_agent(who)
        req = get_or_404(request_id)
        if who.id != req.recipient_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail=f"Only {req.recipient_id} can report progress on request {request_id}.")
        if req.status not in (RequestStatus.DELIVERING, RequestStatus.EXECUTING):
            raise ProductError(409, "request.not_running",
                               f"Request {request_id} is {req.status.value}: progress applies only while it runs.")
        db.mark_mcp_seen(req.recipient_session_id)
        text_ = payload.content.strip()
        db.set_progress(request_id, text_)
        db.log_event(request_id, who.actor, "progress", {"content": text_})
        notify_change(request_id)
        return db.get_request(request_id)

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

        db.mark_mcp_seen(req.recipient_session_id)
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
        acked = ack_request(who, request_id, via="mcp")
        db.mark_mcp_seen(acked.recipient_session_id)
        return acked

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
            cwd=payload.cwd, project_root=payload.project_root, title=payload.title, tmux_session=payload.tmux_session, tmux_pane=payload.tmux_pane,
            tmux_server=payload.tmux_server, agent_pid=payload.agent_pid, agent_started=payload.agent_started,
            model=payload.model,
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
            db.wake_evidence(session_id)  # a desktop wake-up got its evidence: the session started
            if payload.mcp is not None:
                db.set_mcp_config_state(session_id, payload.mcp)
            if payload.start_source == "clear":
                result["node_id"] = db.reattach_after_clear(session_id)
        elif payload.event == "prompt":
            if session.prompt_count == 0 and payload.continues and payload.continues != session_id:
                old = db.get_session(payload.continues)
                if (
                    old is not None
                    and old.agent_id == who.id
                    and old.cwd == (payload.cwd or session.cwd)
                    and old.continued_to is None
                ):
                    if db.continue_session(old.id, session_id):
                        result["continued_from"] = old.id
                        notify_session(old.id)
            db.increment_prompt_count(session_id)
            turn_request_id = None
            paused = paused_request(session)
            if payload.resumes and paused is not None:
                # RFC-16: the turn continues the paused work of the same request.
                turn_request_id = paused
                db.log_event(paused, f"{who.actor}:hook", "turn_resumed", {})
                notify_change(paused)
            elif payload.request_id is not None:
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
            if session.state != "busy":
                # A new turn began: the previous one is over, and any answered
                # request still holding the queue ended with it.
                for released in db.release_on_observed_turn_end(session_id):
                    notify_change(released)
            db.set_session_state(session_id, "busy", turn_request_id)
            result["turn_request_id"] = turn_request_id
        elif payload.event == "stop" and payload.pause and session.turn_request_id is not None \
                and paused_request(session) is not None:
            # RFC-16: not the end of the work. The request stays open, the
            # session busy (nothing else is delivered meanwhile) and no
            # implicit reply is taken from a progress message.
            db.log_event(session.turn_request_id, f"{who.actor}:hook", "turn_paused", {"reason": payload.pause})
            if payload.pause == "app_review":
                notify_app_review(session)
            result["paused_for"] = session.turn_request_id
            notify_change(session.turn_request_id)
        elif payload.event == "stop":
            was_busy = session.state == "busy"
            is_placet = (session.turn_request_id is not None) or bool(db.steered_in_turn(session_id))
            db.set_session_state(session_id, "idle")
            if was_busy:
                notify_turn_end(session, payload.last_message, is_placet=is_placet)
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
            if not payload.pause:
                # A turn end Placet saw (a pause is not one): answered requests
                # still holding the queue ended with it.
                for released in db.release_on_observed_turn_end(session_id):
                    notify_change(released)
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
                db.wake_evidence(session_id)  # the waiter is listening: a pending wake-up is over
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

    async def session_command(session_id: str, command: str, level: Optional[str], who: Principal,
                              confirm_uncertain: bool = False) -> Dict[str, object]:
        """
        RF-62: /compact or /effort typed into a Claude Code CLI's tmux pane.
        Only a supported, open, idle session; a fixed line built here, sent
        through the bridge; audited.
        """
        require_human(who)
        session = db.get_session(session_id)
        if session is None:
            raise ProductError(404, "session.not_found", "Session not found")
        if session.excluded:
            raise ProductError(409, "session.excluded", "This project is excluded from Placet (.placet-off).")
        if session.ended_at is not None:
            raise ProductError(409, "session.closed", "The session is closed.")
        state = session.controls.get(command, "unsupported")
        if state == "pc_only":
            raise ProductError(409, "session.control_pc_only", "This control is available only on the PC for this session.")
        if state != "supported":
            raise ProductError(409, "session.control_unsupported", "This provider offers no remote control for this.")
        if command == "effort" and level not in effort_levels(session.provider):
            raise ProductError(422, "session.effort_invalid", "Not an effort level this provider accepts.",
                               {"levels": list(effort_levels(session.provider))})
        if session.state != "idle":
            raise ProductError(409, "session.busy", "The session is working: wait for the turn to end.")
        # The pane must still be this session's: a later session in the same pane
        # (a new conversation, a resume) makes this one stale, a tie is ambiguous.
        occupants = db.pane_occupants(session.tmux_pane)
        if not occupants or occupants[0].id != session.id:
            raise ProductError(409, "session.pane_stale", "Another session now runs in this terminal.",
                               {"current": occupants[0].id if occupants else None})
        if len(occupants) > 1 and occupants[1].last_seen_at == occupants[0].last_seen_at:
            raise ProductError(409, "session.pane_ambiguous", "Two sessions are equally recent in this terminal.")
        line = command_text(command, level, session.provider)
        params = {"agent_id": session.agent_id, "tmux_session": session.tmux_session,
                  "tmux_pane": session.tmux_pane, "tmux_server": session.tmux_server,
                  "agent_pid": session.agent_pid, "agent_started": session.agent_started,
                  "session_id": session.id, "command": command}
        if level is not None:
            params["level"] = level

        def local():
            from bridge.session_commands import CommandError, run_command
            from bridge.tmux_controller import SessionSpec, TmuxController
            agent = config.get_agent(session.agent_id)
            spec = SessionSpec(name=agent.tmux_session, idle_patterns=agent.idle_patterns,
                               busy_patterns=agent.busy_patterns) if agent else None
            try:
                sent_, confirm_ = run_command(
                    TmuxController(), spec, session.tmux_session, session.tmux_pane, command, level,
                    identity={"tmux_server": session.tmux_server, "agent_pid": session.agent_pid,
                              "agent_started": session.agent_started})
                return {"sent": sent_, "needs_confirmation": confirm_}
            except CommandError as e:
                raise ProductError(409, f"session_command.{e.code}", str(e)) from None

        errors = {"wrong_session": (409, "Not the tmux session configured for this agent."),
                  "tmux_missing": (409, "The tmux session is not running."),
                  "not_idle": (409, "The session is not at an empty prompt."),
                  "user_typing": (409, "Someone is typing in the session."),
                  "check_failed": (409, "tmux could not tell whether someone is typing."),
                  "identity_unknown": (409, "The session did not report which process runs in its terminal."),
                  "server_mismatch": (409, "tmux was restarted: this pane belongs to another server."),
                  "agent_gone": (409, "The session's agent process is not running."),
                  "agent_not_in_pane": (409, "The session's agent does not run in this pane."),
                  "invalid": (422, "Not a fixed session command."),
                  "send_failed": (502, "tmux refused the keys; nothing was typed."),
                  "enter_failed": (502, "The text was typed but Enter was not sent."),
                  "unverified": (502, "Enter was sent but the screen cannot be read."),
                  "type_timeout": (502, "tmux did not answer while typing."),
                  "expired": (409, "The command waited too long for the terminal and was dropped."),
                  "unknown_job": (409, "The bridge is too old for this command.")}
        # Certainly not typed: every check failed, or the job never reached the bridge. Anything
        # else (lost reply, timeout, text typed without Enter, an unexpected error) is uncertain.
        refused = {"bridge.offline", "bridge.not_taken", "session_command.expired", "session_command.unknown_job",
                   "session_command.wrong_session", "session_command.tmux_missing",
                   "session_command.not_idle", "session_command.user_typing", "session_command.check_failed",
                   "session_command.identity_unknown", "session_command.server_mismatch",
                   "session_command.agent_gone", "session_command.agent_not_in_pane",
                   "session_command.invalid",
                   "session_command.send_failed"}
        # After an uncertain outcome the command may already have been applied: repeating it needs the
        # user's explicit confirmation, kept here so that it holds for every tab and device.
        last = db.last_session_control(session_id)
        if last and last["state"] == "uncertain" and not confirm_uncertain \
                and datetime.now(timezone.utc) - datetime.fromisoformat(last["at"]) < UNCERTAIN_CONFIRM_WINDOW:
            raise ProductError(409, "session.control_uncertain",
                               "The previous command's outcome is uncertain: check the session, then confirm.",
                               {"control_id": last["id"], "command": last["command"]})
        if db.session_has_running_request(session.id):
            raise ProductError(409, "session.has_request_running",
                               "A Placet request is being delivered to or executed by this session.")
        params["deadline"] = time.time() + bridge_jobs_module.JOB_TIMEOUT_SECONDS
        try:
            control_id = db.begin_session_control(session_id, who.actor, line)
        except ControlPending:
            raise ProductError(409, "session.control_pending", "A control for this session is still being sent.") from None
        try:
            outcome = await bridge_call("session_command", params, local, errors)
        except ProductError as e:
            if e.code in refused:
                db.settle_session_control(control_id, "refused")
                raise
            db.settle_session_control(control_id, "uncertain")
            raise ProductError(502, "session_command.uncertain",
                               "The outcome is uncertain: check the session before repeating.",
                               {"control_id": control_id}) from None
        except Exception:
            db.settle_session_control(control_id, "uncertain")
            raise
        # A dialog is waiting for the user on the PC: neither "sent" nor uncertain, and not retried.
        state = "needs_confirmation" if outcome.get("needs_confirmation") else "sent"
        db.settle_session_control(control_id, state)
        notify_session(session_id)
        return {"ok": True, "command": line, "control_id": control_id, "state": state}

    @app.post("/api/sessions/{session_id}/compact")
    async def compact_session(session_id: str, payload: Optional[ControlPayload] = None,
                              who: Principal = Depends(principal)):
        return await session_command(session_id, "compact", None, who, bool(payload and payload.confirm_uncertain))

    @app.post("/api/sessions/{session_id}/effort")
    async def set_session_effort(session_id: str, payload: EffortPayload, who: Principal = Depends(principal)):
        return await session_command(session_id, "effort", payload.level, who, payload.confirm_uncertain)

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
        project_context = None
        if body.project_id is not None:
            project = db.get_project(body.project_id)
            if project is None:
                raise ProductError(404, "project.not_found", "Project not found")
            if not session.node_id or not db.is_node_in_project(project.id, session.node_id):
                raise ProductError(409, "project.node_not_in_project", "The session's node is not part of this project.")
            node_inst = db.get_project_node_instructions(project.id, session.node_id)
            outgoing = db.list_node_project_outgoing_relations(project.id, session.node_id)
            block = compose_project_instructions(project.name, project.instructions, node_inst, outgoing)
            if block:
                project_context = block
        if body.scheduled_at is not None:
            if body.mode == "now":
                raise ProductError(422, "schedule.steer_conflict", "A message in 'now' mode cannot be scheduled.")
            validate_scheduled_time(body.scheduled_at)
        cfg = usage_guard()
        rec_provider = session.provider
        sched_at = body.scheduled_at
        hold_for_usage = False
        if (
            body.mode != "now"
            and sched_at is None
            and rec_provider
            and db.is_provider_over_limit(rec_provider, cfg["five_hour"], cfg["seven_day"], check_near_limit=cfg["enabled"])
        ):
            if cfg.get("deliver_at_reset", False):
                rec_reset = db.provider_reset_time(
                    rec_provider, cfg["five_hour"], cfg["seven_day"], check_near_limit=cfg["enabled"]
                )
                if rec_reset is not None:
                    sched_at = rec_reset.isoformat()
                else:
                    hold_for_usage = True
            else:
                hold_for_usage = True

        created = db.create_request(
            RequestCreate(
                sender_id="human", recipient_id=session.agent_id, question_type=QuestionType.MESSAGE,
                prompt=prompt, context=mention_context, recipient_session_id=session.id, priority=Priority.NORMAL,
                project_id=body.project_id, project_context=project_context,
                scheduled_at=sched_at,
            ),
            actor=who.actor,
        )
        if body.mode == "now":
            db.set_delivery_mode(created.id, "now")
            created = db.get_request(created.id)

        if hold_for_usage:
            db.log_event(created.id, "system:limits", "automation_limited",
                         {"reason": "usage", "provider": rec_provider})
            notify_change(created.id)
            return created

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

    # --- Nodes (RFC-02) -------------------------------------------------------

    def node_name_taken(name: str) -> ProductError:
        return ProductError(409, "node.name_taken", "Another node already has this name.")

    @app.get("/api/nodes", response_model=List[NodeModel])
    async def list_nodes(who: Principal = Depends(principal)):
        require_human(who)
        return db.list_nodes()

    @app.post("/api/nodes", response_model=NodeModel, status_code=201)
    async def create_node(body: NodeCreate, who: Principal = Depends(principal)):
        """Names a session: it becomes a node other sessions can address."""
        require_human(who)
        try:
            node = db.create_node(body.name.strip(), body.session_id)
        except NodeNameTaken:
            raise node_name_taken(body.name) from None
        if node is None:
            raise ProductError(404, "session.not_found", "Session not found")
        notify_session(body.session_id)
        return node

    @app.patch("/api/nodes/{node_id}", response_model=NodeModel)
    async def rename_node(node_id: int, body: NodeRename, who: Principal = Depends(principal)):
        require_human(who)
        try:
            found = db.rename_node(node_id, body.name.strip())
        except NodeNameTaken:
            raise node_name_taken(body.name) from None
        if not found:
            raise ProductError(404, "node.not_found", "Node not found")
        node = db.get_node(node_id)
        if node.session_id:
            notify_session(node.session_id)
        return node

    @app.delete("/api/nodes/{node_id}", status_code=204)
    async def delete_node(node_id: int, who: Principal = Depends(principal)):
        """Removes the name; the sessions stay, detached."""
        require_human(who)
        if db.get_node(node_id) is None:
            raise ProductError(404, "node.not_found", "Node not found")
        for session_id in db.delete_node(node_id):
            notify_session(session_id)
        return Response(status_code=204)

    @app.put("/api/sessions/{session_id}/node", response_model=SessionModel)
    async def attach_session(session_id: str, body: NodeAttach, who: Principal = Depends(principal)):
        """Attaches a session to an existing node, or detaches it (node_id null)."""
        require_human(who)
        outcome = db.attach_session(session_id, body.node_id)
        if outcome == "session":
            raise ProductError(404, "session.not_found", "Session not found")
        if outcome == "node":
            raise ProductError(404, "node.not_found", "Node not found")
        if outcome == "provider":
            raise ProductError(409, "node.other_provider", "The node belongs to another provider.")
        notify_session(session_id)
        return db.get_session(session_id)

    # --- Relation types and relations (RFC-03, RFC-04) ------------------------

    @app.get("/api/relation-types", response_model=List[RelationTypeModel])
    async def list_relation_types(who: Principal = Depends(principal)):
        require_human(who)
        return db.list_relation_types()

    @app.post("/api/relation-types", response_model=RelationTypeModel, status_code=201)
    async def create_relation_type(body: RelationTypeCreate, who: Principal = Depends(principal)):
        require_human(who)
        try:
            return db.create_relation_type(body.name.strip(), body.description.strip())
        except NameTaken:
            raise ProductError(409, "relation_type.name_taken", "Another relation type has this name.") from None

    @app.patch("/api/relation-types/{type_id}", response_model=RelationTypeModel)
    async def update_relation_type(type_id: int, body: RelationTypeUpdate, who: Principal = Depends(principal)):
        require_human(who)
        try:
            found = db.update_relation_type(
                type_id, body.name.strip() if body.name else None,
                body.description.strip() if body.description is not None else None)
        except NameTaken:
            raise ProductError(409, "relation_type.name_taken", "Another relation type has this name.") from None
        if not found:
            raise ProductError(404, "relation_type.not_found", "Relation type not found")
        return db.get_relation_type(type_id)

    @app.delete("/api/relation-types/{type_id}", status_code=204)
    async def delete_relation_type(type_id: int, who: Principal = Depends(principal)):
        require_human(who)
        outcome = db.delete_relation_type(type_id)
        if outcome == "missing":
            raise ProductError(404, "relation_type.not_found", "Relation type not found")
        if outcome == "in_use":
            raise ProductError(409, "relation_type.in_use", "Relations of this type exist.")
        return Response(status_code=204)

    # --- Web Push to the installed PWA ---------------------------------------------

    @app.get("/api/push/key")
    async def push_key(who: Principal = Depends(principal)):
        require_human(who)
        return {"key": await asyncio.to_thread(web_push.public_key)}

    @app.post("/api/push/subscription", status_code=204)
    async def push_subscribe(body: PushSubscription, request: Request, who: Principal = Depends(principal)):
        require_human(who)
        if body.keys is None or not allowed_endpoint(body.endpoint):
            raise ProductError(422, "push.invalid", "Not a browser push subscription.")
        db.add_push_subscription(body.endpoint, body.keys.p256dh, body.keys.auth,
                                 request.headers.get("user-agent"))
        return Response(status_code=204)

    @app.delete("/api/push/subscription", status_code=204)
    async def push_unsubscribe(body: PushSubscription, who: Principal = Depends(principal)):
        require_human(who)
        db.delete_push_subscription(body.endpoint)
        return Response(status_code=204)

    @app.post("/api/push/test")
    async def push_test(who: Principal = Depends(principal)):
        require_human(who)
        lang = db.get_background_language()
        sent = await web_push.send(text("notify.test_title", lang), text("notify.test_body", lang),
                                   "/", "test", "normal")
        return {"sent": sent}

    # --- Automation limits and Stop all (RFC-14) --------------------------------

    @app.get("/api/automation")
    async def get_automation(who: Principal = Depends(principal)):
        require_human(who)
        automatic = sum(1 for r in db.list_relations() if r.approval == "automatic")
        return {**db.get_automation(), "automatic_relations": automatic, "usage_guarded": sorted(usage_guarded())}

    @app.put("/api/automation")
    async def set_automation(body: AutomationLimits, who: Principal = Depends(principal)):
        require_human(who)
        db.set_automation(max_hops=body.max_hops, max_per_hour=body.max_per_hour)
        _broadcast(("automation", 0))
        return await get_automation(who)

    @app.post("/api/automation/stop")
    async def stop_automation(who: Principal = Depends(principal)):
        """Stop all: no automatic delivery until resumed; queued ones go back to review."""
        require_human(who)
        db.set_automation(paused=True)
        held = db.hold_automatic(who.actor)
        for request_id in held:
            notify_change(request_id)
        _broadcast(("automation", 0))
        return {**(await get_automation(who)), "held": held}

    @app.post("/api/automation/resume")
    async def resume_automation(who: Principal = Depends(principal)):
        require_human(who)
        db.set_automation(paused=False)
        _broadcast(("automation", 0))
        return await get_automation(who)

    @app.get("/api/settings/relation-rule", response_model=RelationRule)
    async def get_relation_rule(who: Principal = Depends(principal)):
        require_human(who)
        return {"rule": db.get_relation_rule()}

    @app.put("/api/settings/relation-rule", response_model=RelationRule)
    async def set_relation_rule(body: RelationRule, who: Principal = Depends(principal)):
        """The general rule agents must read before each automatic relation (RFC-06)."""
        require_human(who)
        db.set_relation_rule(body.rule.strip())
        return {"rule": db.get_relation_rule()}

    @app.get("/api/agents/me/relations")
    async def my_relations(session_id: Optional[str] = None, who: Principal = Depends(principal)):
        """
        RFC-08: the relations the calling session can use, with each type's
        description and delivery policy, and the general rule.
        """
        require_agent(who)
        sid = session_id or created_sender_session(who)
        session = db.get_session(sid) if sid else None
        if session is not None and session.agent_id != who.id:
            session = None
        if session is not None:
            db.mark_mcp_seen(session.id)  # RF-27: placet_list_relations carried this session
        out: Dict[str, object] = {"session_id": session.id if session else None,
                                  "node": session.node_name if session else None,
                                  "rule": db.get_relation_rule(), "relations": []}
        if session is None:
            out["note"] = "This session could not be identified (pass session_id); no relations listed."
        elif session.node_id is None:
            out["note"] = "This session has no node: the user has not named it, so it has no relations."
        else:
            for rel in db.outgoing_relations(session.node_id):
                node = db.get_node(rel.to_node)
                target = db.get_session(node.session_id) if node and node.session_id else None
                policy = db.get_relation_effective_policy(rel.id)
                out["relations"].append({
                    **relation_brief(rel),
                    "target_agent": target.agent_id if target else None,
                    "target_session": target.id if target else None,
                    "target_reachable": bool(target and target.reachable),
                    "reply_policy": "automatic" if reply_is_automatic(rel) else "manual",
                    "projects": policy["projects"],
                    "rule": policy["rule"],
                })
        return out

    @app.get("/api/relations", response_model=List[RelationModel])
    async def list_relations(node_id: Optional[int] = None, who: Principal = Depends(principal)):
        require_human(who)
        return db.list_relations(node_id)

    @app.post("/api/relations", response_model=RelationModel, status_code=201)
    async def create_relation(body: RelationCreate, who: Principal = Depends(principal)):
        """A relation A -> B of a type; `automatic` delivers without review (RFC-05)."""
        require_human(who)
        outcome = db.create_relation(body.from_node, body.to_node, body.type_id, body.approval, body.reply_approval)
        if outcome == "nodes":
            raise ProductError(404, "node.not_found", "Node not found")
        if outcome == "type":
            raise ProductError(404, "relation_type.not_found", "Relation type not found")
        if outcome == "self":
            raise ProductError(422, "relation.self", "A node cannot have a relation with itself.")
        if outcome == "exists":
            raise ProductError(409, "relation.exists", "This relation already exists.")
        return outcome

    @app.patch("/api/relations/{relation_id}", response_model=RelationModel)
    async def update_relation(relation_id: int, body: RelationUpdate, who: Principal = Depends(principal)):
        require_human(who)
        if db.get_relation(relation_id) is None:
            raise ProductError(404, "relation.not_found", "Relation not found")
        if body.approval is not None:
            db.set_relation_approval(relation_id, body.approval)
        if body.reply_approval is not None:
            db.set_relation_reply_approval(relation_id, body.reply_approval)
        return db.get_relation(relation_id)

    @app.delete("/api/relations/{relation_id}", status_code=204)
    async def delete_relation(relation_id: int, who: Principal = Depends(principal)):
        require_human(who)
        if not db.delete_relation(relation_id):
            raise ProductError(404, "relation.not_found", "Relation not found")
        return Response(status_code=204)

    @app.get("/api/projects", response_model=List[ProjectSummaryModel])
    async def list_projects(node_id: Optional[int] = Query(None), who: Principal = Depends(principal)):
        require_human(who)
        return db.list_projects(node_id=node_id)

    @app.post("/api/projects", response_model=ProjectModel, status_code=201)
    async def create_project(body: ProjectCreate, who: Principal = Depends(principal)):
        require_human(who)
        try:
            return db.create_project(
                name=body.name,
                description=body.description,
                relation_rule=body.relation_rule,
                max_auto_hops=body.max_auto_hops,
                max_auto_per_hour=body.max_auto_per_hour,
                instructions=body.instructions,
            )
        except ProjectNameTaken:
            raise ProductError(409, "project.name_taken", "A project with this name already exists.")

    @app.get("/api/projects/{project_id}", response_model=ProjectDetailModel)
    async def get_project(project_id: int, who: Principal = Depends(principal)):
        require_human(who)
        p = db.get_project(project_id)
        if not p:
            raise ProductError(404, "project.not_found", "Project not found")
        return p

    @app.patch("/api/projects/{project_id}", response_model=ProjectModel)
    async def update_project(project_id: int, body: ProjectUpdate, who: Principal = Depends(principal)):
        require_human(who)
        try:
            p = db.update_project(
                project_id,
                name=body.name,
                description=body.description,
                relation_rule=body.relation_rule,
                max_auto_hops=body.max_auto_hops,
                max_auto_per_hour=body.max_auto_per_hour,
                instructions=body.instructions,
                fields_set=body.model_fields_set,
            )
        except ProjectNameTaken:
            raise ProductError(409, "project.name_taken", "A project with this name already exists.")
        if not p:
            raise ProductError(404, "project.not_found", "Project not found")
        return p

    @app.delete("/api/projects/{project_id}", status_code=204)
    async def delete_project(project_id: int, who: Principal = Depends(principal)):
        require_human(who)
        if not db.delete_project(project_id):
            raise ProductError(404, "project.not_found", "Project not found")
        return Response(status_code=204)

    @app.put("/api/projects/{project_id}/nodes/{node_id}")
    async def add_project_node(project_id: int, node_id: int, who: Principal = Depends(principal)):
        require_human(who)
        if not db.get_project(project_id):
            raise ProductError(404, "project.not_found", "Project not found")
        if not db.get_node(node_id):
            raise ProductError(404, "node.not_found", "Node not found")
        db.add_node_to_project(project_id, node_id)
        return {"ok": True}

    @app.patch("/api/projects/{project_id}/nodes/{node_id}")
    async def update_project_node(project_id: int, node_id: int, body: ProjectNodeUpdate, who: Principal = Depends(principal)):
        require_human(who)
        if not db.get_project(project_id):
            raise ProductError(404, "project.not_found", "Project not found")
        if not db.get_node(node_id):
            raise ProductError(404, "node.not_found", "Node not found")
        val = body.instructions.strip() if body.instructions and body.instructions.strip() else None
        if not db.update_project_node_instructions(project_id, node_id, val):
            raise ProductError(404, "project.node_not_in_project", "Node not in project.")
        return {"ok": True, "instructions": val}

    @app.delete("/api/projects/{project_id}/nodes/{node_id}")
    async def remove_project_node(project_id: int, node_id: int, who: Principal = Depends(principal)):
        require_human(who)
        if not db.get_project(project_id):
            raise ProductError(404, "project.not_found", "Project not found")
        ok, removed = db.remove_node_from_project(project_id, node_id)
        return {"ok": True, "removed_relations": removed}

    @app.put("/api/projects/{project_id}/relations/{relation_id}")
    async def add_project_relation(project_id: int, relation_id: int, who: Principal = Depends(principal)):
        require_human(who)
        if not db.get_project(project_id):
            raise ProductError(404, "project.not_found", "Project not found")
        if not db.get_relation(relation_id):
            raise ProductError(404, "relation.not_found", "Relation not found")
        ok, shared_with = db.add_relation_to_project(project_id, relation_id)
        return {"ok": True, "shared_with": shared_with}

    @app.delete("/api/projects/{project_id}/relations/{relation_id}")
    async def remove_project_relation(project_id: int, relation_id: int, who: Principal = Depends(principal)):
        require_human(who)
        if not db.get_project(project_id):
            raise ProductError(404, "project.not_found", "Project not found")
        db.remove_relation_from_project(project_id, relation_id)
        return {"ok": True}

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
        except BridgeNotTaken:
            raise ProductError(504, "bridge.not_taken", "The bridge did not pick the job up: nothing was done.") from None
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
                {"id": s.id, "cwd": s.cwd, **({"force": True} if db.wake_forced(s.id) else {})}
                for s in db.claude_sessions_to_wake()
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
            "claude_auto_renew": db.get_setting("claude_auto_renew") == "true",
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

    class WakeReport(BaseModel):
        outcome: Literal["open_requested", "failed"]

    @app.post("/api/bridge/sessions/{session_id}/wake")
    async def bridge_wake_report(session_id: str, body: WakeReport, who: Principal = Depends(principal)):
        """
        What happened to a Claude Desktop continue link: handed to the app (the launcher's rc=0 does
        not mean the session started) or certainly not. Counts against the episode's budget.
        """
        require_bridge(who)
        if not db.report_wake(session_id, body.outcome):
            raise HTTPException(status_code=404, detail="Session not found")
        notify_session(session_id)
        if db.get_session(session_id).wake_state == "wake_failed":
            alert_wake(session_id)
        return {"session": session_id, "outcome": body.outcome}

    class WakeCauseReport(BaseModel):
        cause: Optional[Literal["warm_capacity_denied"]] = None
        observed_at: Optional[datetime] = None
        attempt_at: datetime

    @app.post("/api/bridge/sessions/{session_id}/wake-cause")
    async def bridge_wake_cause(session_id: str, body: WakeCauseReport, who: Principal = Depends(principal)):
        """
        Why the app did not start a session whose link it accepted, as the bridge read in the app's log:
        a closed code only, for the current attempt. Informs the notice; it changes nothing.
        """
        require_bridge(who)
        if not db.report_wake_cause(session_id, body.cause, body.observed_at, body.attempt_at):
            raise HTTPException(status_code=404, detail="Session not found")
        notify_session(session_id)
        return {"session": session_id, "cause": body.cause}

    @app.post("/api/sessions/{session_id}/wake")
    async def retry_wake(session_id: str, who: Principal = Depends(principal)):
        """
        "Riprova apertura": the user asks again to open a Claude Desktop session that never started
        listening. A new episode (one automatic open, the bridge's cooldown ignored once). The
        message waiting for it is untouched.
        """
        require_human(who)
        session = db.get_session(session_id)
        if session is None:
            raise ProductError(404, "session.not_found", "Session not found")
        if session.provider != "claude-code" or session.entrypoint != "claude-desktop":
            raise ProductError(409, "session.wake_unsupported", "Only a Claude Desktop session can be opened this way.")
        if session.excluded:
            raise ProductError(409, "session.excluded", "This project is excluded from Placet (.placet-off).")
        if session.ended_at is not None:
            raise ProductError(409, "session.closed", "The session is closed.")
        if session.desktop_archived:
            raise ProductError(409, "session.desktop_archived", "The conversation is archived in Claude: restore it in the app.")
        if session.reachable:
            raise ProductError(409, "session.already_reachable", "The session is already listening.")
        if session.wake_state in ("open_requested", "waiting_for_session"):
            raise ProductError(409, "session.wake_in_progress", "The opening was just requested: wait for the window to end.")
        if not db.inbox_has_message(session, explicit_only=True):
            raise ProductError(409, "session.nothing_to_wake", "No message is waiting for this session.")
        db.request_wake_retry(session_id)
        notify_session(session_id)
        return {"session": session_id, "wake_state": None}

    class DesktopStateReport(BaseModel):
        archived: List[str] = Field(default_factory=list, max_length=2000)
        not_archived: List[str] = Field(default_factory=list, max_length=2000)
        # Loaded Claude Desktop engines against the app's cap: numbers only, both or neither.
        engines: Optional[int] = Field(default=None, ge=0, le=1000)
        cap: Optional[int] = Field(default=None, ge=1, le=1000)

    @app.post("/api/bridge/desktop-state")
    async def bridge_desktop_state(body: DesktopStateReport, who: Principal = Depends(principal)):
        """What the Claude Desktop app records, read by the bridge: archived conversations (RF-34)."""
        require_bridge(who)
        changed = db.set_desktop_archived(body.archived, body.not_archived)
        for session_id in changed:
            notify_session(session_id)
        if body.engines is not None and body.cap is not None:
            db.set_desktop_capacity(body.engines, body.cap)
        return {"changed": changed}

    @app.get("/api/claude-desktop/capacity")
    async def claude_desktop_capacity(response: Response, who: Principal = Depends(principal)):
        """Loaded Claude Desktop engines against the app's cap, as the bridge last counted (empty if never)."""
        require_human(who)
        response.headers["Cache-Control"] = "no-store"
        return db.get_desktop_capacity()

    @app.post("/api/bridge/titles")
    async def bridge_titles(payload: TitlesReport, who: Principal = Depends(principal)):
        """Session titles the bridge read from the providers' apps."""
        require_bridge(who)
        changed = db.set_session_titles(payload.titles)
        for session_id in changed:
            notify_session(session_id)
        return {"changed": changed}

    @app.post("/api/bridge/usage")
    async def bridge_usage(payload: UsageReport, who: Principal = Depends(principal)):
        """Usage windows per provider (RF-60), read by the bridge every minute."""
        require_bridge(who)
        for item in payload.providers:
            db.set_usage(item.provider, item.model_dump_json())
            db.record_usage_samples(item.provider, item.buckets, item.observed_at)
        _broadcast(("usage", None))
        return {"providers": [p.provider for p in payload.providers]}

    @app.get("/api/usage", response_model=List[ProviderUsage])
    async def get_usage(who: Principal = Depends(principal)):
        require_human(who)
        return stored_usage()

    @app.post("/api/usage/claude/renew")
    async def renew_claude_usage(who: Principal = Depends(principal)):
        require_human(who)

        def local():
            from bridge.usage import renew_claude_credentials, claude_usage, reset_usage_caches
            if not renew_claude_credentials():
                raise ProductError(502, "claude_renew.failed", "Claude credential renewal failed.")
            reset_usage_caches()
            row = claude_usage()
            if row is not None:
                db.set_usage(row.provider, row.model_dump_json())
                db.record_usage_samples(row.provider, row.buckets, row.observed_at)
                _broadcast(("usage", None))
            return {"renewed": True}

        errors = {"failed": (502, "Claude credential renewal failed.")}
        return await bridge_call("claude_renew", {}, local, errors)

    @app.get("/api/stats")
    async def get_stats_endpoint(
        from_: Optional[str] = Query(None, alias="from"),
        to: Optional[str] = None,
        tz: str = "UTC",
        who: Principal = Depends(principal),
    ):
        """RF-64: statistics and usage history in range [from, to] localized to tz."""
        require_human(who)
        lang = db.get_background_language()

        import zoneinfo
        try:
            target_tz = zoneinfo.ZoneInfo(tz)
        except Exception:
            raise ProductError(422, "stats.invalid_timezone", text("stats.invalid_timezone", lang, tz=tz), {"tz": tz})

        today_local = datetime.now(target_tz).date()

        if to is not None:
            try:
                to_date = datetime.strptime(to, "%Y-%m-%d").date()
            except ValueError:
                raise ProductError(422, "stats.invalid_date", text("stats.invalid_date", lang, date=to), {"date": to})
        else:
            to_date = today_local

        if from_ is not None:
            try:
                from_date = datetime.strptime(from_, "%Y-%m-%d").date()
            except ValueError:
                raise ProductError(422, "stats.invalid_date", text("stats.invalid_date", lang, date=from_), {"date": from_})
        else:
            from_date = to_date - timedelta(days=6)

        if from_date > to_date:
            raise ProductError(422, "stats.invalid_range", text("stats.invalid_range", lang))

        if (to_date - from_date).days > 400:
            raise ProductError(422, "stats.range_too_large", text("stats.range_too_large", lang))

        thresholds = usage_guard()
        return db.get_stats(from_date, to_date, tz_name=tz, usage_thresholds=thresholds)

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
                    if kind == "usage":
                        yield {"event": "usage_update",
                               "data": json.dumps([u.model_dump(mode="json") for u in stored_usage()])}
                        continue
                    if kind == "automation":
                        yield {"event": "automation_update", "data": json.dumps(db.get_automation())}
                        continue
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
