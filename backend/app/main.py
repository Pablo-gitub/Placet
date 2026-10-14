import os
import asyncio
import contextlib
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Dict, Set
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks, status, Request, Depends
from fastapi.middleware.cors import CORSMiddleware

from fastapi.responses import FileResponse
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
    RESULT_GRACE_SECONDS,
    RESULT_MAX_AGE_SECONDS,
    decision_error,
    is_settled,
)
from backend.app.notifier import NtfyConfig, send_ntfy_notification

logger = logging.getLogger(__name__)

# Upper bound for a single sleep while long-polling: changes made by other
# processes (the bridge writes to SQLite directly) are not signalled, so the
# waiter re-reads the database at least this often.
LONG_POLL_RECHECK_SECONDS = 5.0
EXPIRY_SWEEP_SECONDS = 30.0


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

    def notify_change(req_id: int):
        # Must run on the event loop thread: all endpoints touching it are async.
        for ev in request_waiters.get(req_id, ()):
            ev.set()
        for q in list(sse_subscribers):
            try:
                q.put_nowait(req_id)
            except Exception:
                pass

    def expire_and_notify():
        for req_id in db.expire_stale_requests():
            notify_change(req_id)

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

        if req.priority is None:
            req = req.model_copy(update={
                "priority": Priority.HIGH if req.recipient_id == "human" else Priority.NORMAL
            })

        # Check if approval required
        needs_approval = config.requires_approval(req.sender_id, req.recipient_id)

        created = db.create_request(req, actor=who.actor)

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

        updated = db.update_request_decision(
            request_id=request_id,
            status=decision.status,
            edited_prompt=decision.edited_prompt,
            selected_options=decision.selected_options,
            custom_text=decision.custom_text,
            actor=who.actor,
        )
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

        if req.status not in (RequestStatus.APPROVED, RequestStatus.EXECUTING):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Request {request_id} is in {req.status.value} state, not in APPROVED or EXECUTING state.",
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
                detail=f"Request {request_id} is in {current.status.value} state, not in APPROVED or EXECUTING state.",
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


    # --- Local bridge (tmux injection and outcome delivery) -----------------

    @app.get("/api/bridge/work")
    async def bridge_work(who: Principal = Depends(principal)):
        """
        What the bridge should do now: approved agent-to-agent requests to
        inject into the recipient's session (oldest first), and settled
        outcomes to deliver to the sender's session.
        """
        require_bridge(who)
        expire_and_notify()
        now = datetime.now(timezone.utc)
        injections = [
            r for r in db.list_requests(status=RequestStatus.APPROVED, oldest_first=True)
            if r.recipient_id != "human"
        ]
        results = db.list_undelivered_results(
            settled_before=now - timedelta(seconds=RESULT_GRACE_SECONDS),
            settled_after=now - timedelta(seconds=RESULT_MAX_AGE_SECONDS),
        )
        return {"injections": injections, "results": results}

    @app.post("/api/bridge/requests/{request_id}/injected", response_model=RequestModel)
    async def bridge_injected(request_id: int, who: Principal = Depends(principal)):
        require_bridge(who)
        get_or_404(request_id)
        updated = db.update_request_decision(
            request_id=request_id, status=RequestStatus.EXECUTING, actor=who.actor
        )
        if updated is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Request {request_id} is not APPROVED.",
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
                    req_id = await queue.get()
                    req = db.get_request(req_id)
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
