import os
import asyncio
import contextlib
import logging
from pathlib import Path
from typing import List, Optional, Dict, Set
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks, status, Request
from fastapi.middleware.cors import CORSMiddleware

from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse


from backend.app.config import PlacetConfig, load_agents_config
from backend.app.database import Database
from backend.app.models import (
    FINAL_STATUSES,
    RequestCreate,
    RequestModel,
    RequestStatus,
    RequestDecision,
    ResponseModel,
)
from backend.app.notifier import send_ntfy_notification

logger = logging.getLogger(__name__)

# Upper bound for a single sleep while long-polling: changes made by other
# processes (the bridge writes to SQLite directly) are not signalled, so the
# waiter re-reads the database at least this often.
LONG_POLL_RECHECK_SECONDS = 5.0
EXPIRY_SWEEP_SECONDS = 30.0


def is_settled(req: RequestModel) -> bool:
    """
    True when a long-polling caller has its answer: a human question is settled
    once decided; an agent-to-agent request only once it reaches a final state
    (the reply arrived, or it was rejected/cancelled/expired).
    """
    if req.recipient_id == "human":
        return req.status != RequestStatus.PENDING
    return req.status in FINAL_STATUSES


class ReplyPayload(RequestDecision):
    responder_id: str
    content: str
    status: RequestStatus = RequestStatus.COMPLETED


def create_app(
    db: Optional[Database] = None,
    config: Optional[PlacetConfig] = None,
    ntfy_topic: Optional[str] = None,
    base_url: str = "http://localhost:8000",
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

    app = FastAPI(title="Placet API", version="1.0.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if db is None:
        db = Database(os.environ.get("PLACET_DB_PATH", "placet.db"))
        db.init_db()

    if config is None:
        config = load_agents_config()

    if ntfy_topic is None:
        ntfy_topic = os.environ.get("PLACET_NTFY_TOPIC", "")

    @app.get("/api/health")
    def health():
        return {"status": "ok", "app": "Placet"}

    @app.get("/api/agents")
    def list_agents():
        return {
            "agents": {k: v.model_dump() for k, v in config.agents.items()},
            "rules": {k: v.model_dump() for k, v in config.rules.items()},
            "description": config.description_markdown,
        }

    @app.post("/api/requests", status_code=status.HTTP_201_CREATED)
    async def create_request(
        req: RequestCreate,
        background_tasks: BackgroundTasks,
        http_request: Request,
        wait_seconds: int = Query(0, ge=0, le=600),
    ):
        # Validate allowed route
        if not config.is_route_allowed(req.sender_id, req.recipient_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Route from {req.sender_id} to {req.recipient_id} not allowed by AGENTS.md rules.",
            )

        # Validate agent token if configured
        agent_spec = config.get_agent(req.sender_id)
        if agent_spec and agent_spec.token:
            auth_header = http_request.headers.get("Authorization")
            expected_token = agent_spec.token
            token_valid = False
            if auth_header and auth_header.startswith("Bearer "):
                token_valid = (auth_header[7:].strip() == expected_token)
            if not token_valid:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=f"Invalid agent token for {req.sender_id}.",
                )

        # Check if approval required
        needs_approval = config.requires_approval(req.sender_id, req.recipient_id)

        created = db.create_request(req)

        if not needs_approval:
            # Auto-approval
            created = db.update_request_decision(
                request_id=created.id,
                status=RequestStatus.APPROVED,
            )
        else:
            # Send mobile notification
            background_tasks.add_task(
                send_ntfy_notification,
                request=created,
                ntfy_topic=ntfy_topic,
                base_url=base_url,
            )
        notify_change(created.id)

        if wait_seconds > 0:
            return await wait_until_settled(created.id, float(wait_seconds))
        return created

    @app.get("/api/requests", response_model=List[RequestModel])
    async def list_requests(
        status: Optional[RequestStatus] = None,
        limit: int = Query(50, ge=1, le=200),
    ):
        expire_and_notify()
        return db.list_requests(status=status, limit=limit)

    @app.get("/api/requests/{request_id}", response_model=RequestModel)
    async def get_request(
        request_id: int,
        wait_seconds: int = Query(0, ge=0, le=600),
    ):
        if wait_seconds > 0:
            req = await wait_until_settled(request_id, float(wait_seconds))
        else:
            expire_and_notify()
            req = db.get_request(request_id)
        if not req:
            raise HTTPException(status_code=404, detail="Request not found")
        return req

    @app.post("/api/requests/{request_id}/decision", response_model=RequestModel)
    async def decide_request(request_id: int, decision: RequestDecision):
        expire_and_notify()
        req = db.get_request(request_id)
        if not req:
            raise HTTPException(status_code=404, detail="Request not found")

        if req.status != RequestStatus.PENDING:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Request {request_id} is in {req.status.value} state, not in PENDING state.",
            )

        updated = db.update_request_decision(
            request_id=request_id,
            status=decision.status,
            edited_prompt=decision.edited_prompt,
            selected_options=decision.selected_options,
            custom_text=decision.custom_text,
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
    async def reply_request(request_id: int, payload: Dict[str, str]):
        expire_and_notify()
        req = db.get_request(request_id)
        if not req:
            raise HTTPException(status_code=404, detail="Request not found")

        if req.status not in (RequestStatus.APPROVED, RequestStatus.EXECUTING):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Request {request_id} is in {req.status.value} state, not in APPROVED or EXECUTING state.",
            )

        responder_id = payload.get("responder_id", req.recipient_id)
        content = payload.get("content", "")

        completed = db.record_reply(
            request_id=request_id,
            responder_id=responder_id,
            content=content,
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
    async def cancel_request(request_id: int):
        expire_and_notify()
        req = db.get_request(request_id)
        if not req:
            raise HTTPException(status_code=404, detail="Request not found")

        if req.status != RequestStatus.PENDING:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot cancel request in {req.status.value} state (must be PENDING).",
            )

        if not db.cancel_request(request_id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot cancel request {request_id}: its state changed concurrently.",
            )
        notify_change(request_id)
        return {"status": "CANCELLED", "id": request_id}


    @app.get("/api/events")
    async def sse_events():
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
