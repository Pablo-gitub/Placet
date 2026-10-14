import os
import asyncio
from pathlib import Path
from typing import List, Optional, Dict
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks, status, Request
from fastapi.middleware.cors import CORSMiddleware

from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse


from backend.app.config import PlacetConfig, load_agents_config
from backend.app.database import Database
from backend.app.models import (
    RequestCreate,
    RequestModel,
    RequestStatus,
    RequestDecision,
    ResponseModel,
)
from backend.app.notifier import send_ntfy_notification


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
    app = FastAPI(title="Placet API", version="1.0.0")

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

    # Event trackers for long-polling requests
    request_events: Dict[int, asyncio.Event] = {}
    sse_subscribers: List[asyncio.Queue] = []

    def notify_change(req_id: int):
        if req_id in request_events:
            request_events[req_id].set()
        # Broadcast to SSE queues
        for q in list(sse_subscribers):
            try:
                q.put_nowait(req_id)
            except Exception:
                pass

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
        request_events[created.id] = asyncio.Event()

        if not needs_approval:
            # Auto-approval
            created = db.update_request_decision(
                request_id=created.id,
                status=RequestStatus.APPROVED,
            )
            notify_change(created.id)
        else:
            # Send mobile notification
            background_tasks.add_task(
                send_ntfy_notification,
                request=created,
                ntfy_topic=ntfy_topic,
                base_url=base_url,
            )
            notify_change(created.id)

        # Handle long-polling if requested
        if wait_seconds > 0:
            try:
                # Wait until decided or completed, or timeout
                await asyncio.wait_for(
                    request_events[created.id].wait(), timeout=float(wait_seconds)
                )
            except asyncio.TimeoutError:
                pass
            finally:
                request_events.pop(created.id, None)
            # Return latest state
            return db.get_request(created.id)

        return created

    @app.get("/api/requests", response_model=List[RequestModel])
    def list_requests(
        status: Optional[RequestStatus] = None,
        limit: int = Query(50, ge=1, le=200),
    ):
        # Periodically expire stale pending requests
        db.expire_stale_requests()
        return db.list_requests(status=status, limit=limit)

    @app.get("/api/requests/{request_id}", response_model=RequestModel)
    def get_request(request_id: int):
        req = db.get_request(request_id)
        if not req:
            raise HTTPException(status_code=404, detail="Request not found")
        return req

    @app.post("/api/requests/{request_id}/decision", response_model=RequestModel)
    def decide_request(request_id: int, decision: RequestDecision):
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
        notify_change(request_id)
        return updated

    @app.post("/api/requests/{request_id}/reply", response_model=RequestModel)
    def reply_request(request_id: int, payload: Dict[str, str]):
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
        notify_change(request_id)
        return completed

    @app.delete("/api/requests/{request_id}")
    def cancel_request(request_id: int):
        req = db.get_request(request_id)
        if not req:
            raise HTTPException(status_code=404, detail="Request not found")

        if req.status != RequestStatus.PENDING:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot cancel request in {req.status.value} state (must be PENDING).",
            )

        cancelled = db.cancel_request(request_id)
        if not cancelled:
            raise HTTPException(
                status_code=400, detail="Cannot cancel request"
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
