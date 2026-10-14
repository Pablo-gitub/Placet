"""
Where the bridge reads its work and records progress.

- HttpStore: talks to the backend REST API with the bridge token. This is
  the production mode: the backend runs as a dedicated user and the bridge
  (running as the user who owns the tmux sessions) has no access to the DB.
- LocalStore: reads and writes the SQLite database directly. Used by the
  single-process development runner (run_all.py) and by tests.
"""
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Protocol, Tuple

import httpx

from backend.app.config import PlacetConfig, load_agents_config
from backend.app.database import Database
from bridge.tmux_controller import SessionSpec
from backend.app.models import (
    RESULT_GRACE_SECONDS,
    RESULT_MAX_AGE_SECONDS,
    RequestModel,
    RequestStatus,
)

logger = logging.getLogger(__name__)

BRIDGE_ACTOR = "bridge"


Work = Tuple[List[RequestModel], List[RequestModel]]


class BridgeStore(Protocol):
    def work(self) -> Work:
        """(approved requests to inject, settled outcomes to deliver)"""
        ...

    def mark_injected(self, request_id: int) -> bool: ...
    def claim_delivery(self, request_id: int) -> bool: ...
    def release_delivery(self, request_id: int) -> None: ...
    def session_for(self, agent_id: str) -> Optional[SessionSpec]: ...


class LocalStore:
    def __init__(self, db: Optional[Database] = None, config: Optional[PlacetConfig] = None):
        self.db = db or Database(os.environ.get("PLACET_DB_PATH", "placet.db"))
        self.config = config or load_agents_config()

    def work(self) -> Work:
        now = datetime.now(timezone.utc)
        injections = [
            r for r in self.db.list_requests(status=RequestStatus.APPROVED, oldest_first=True)
            if r.recipient_id != "human"
        ]
        results = self.db.list_undelivered_results(
            settled_before=now - timedelta(seconds=RESULT_GRACE_SECONDS),
            settled_after=now - timedelta(seconds=RESULT_MAX_AGE_SECONDS),
        )
        return injections, results

    def mark_injected(self, request_id: int) -> bool:
        return self.db.update_request_decision(
            request_id, RequestStatus.EXECUTING, actor=BRIDGE_ACTOR
        ) is not None

    def claim_delivery(self, request_id: int) -> bool:
        return self.db.mark_delivered(request_id, actor=BRIDGE_ACTOR, via="tmux")

    def release_delivery(self, request_id: int) -> None:
        self.db.clear_delivered(request_id, actor=BRIDGE_ACTOR)

    def session_for(self, agent_id: str) -> Optional[SessionSpec]:
        agent = self.config.get_agent(agent_id)
        if not agent:
            return None
        return SessionSpec(agent.tmux_session, agent.idle_patterns, agent.busy_patterns)


class HttpStore:
    AGENTS_CACHE_SECONDS = 60.0

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        client: Optional[httpx.Client] = None,
    ):
        base_url = (base_url or os.environ.get("PLACET_API_URL", "http://127.0.0.1:8000")).rstrip("/")
        token = token if token is not None else os.environ.get("PLACET_BRIDGE_TOKEN", "")
        if not token:
            raise SystemExit("PLACET_BRIDGE_TOKEN is not set: the bridge cannot authenticate.")
        self.client = client or httpx.Client(timeout=10.0)
        self.client.base_url = base_url
        self.client.headers["Authorization"] = f"Bearer {token}"
        self._sessions: Dict[str, SessionSpec] = {}
        self._sessions_at = 0.0

    def work(self) -> Work:
        resp = self.client.get("/api/bridge/work")
        resp.raise_for_status()
        data = resp.json()
        return (
            [RequestModel(**r) for r in data["injections"]],
            [RequestModel(**r) for r in data["results"]],
        )

    def _post_ok(self, method: str, path: str) -> bool:
        resp = self.client.request(method, path)
        if resp.status_code == 409:
            return False
        resp.raise_for_status()
        return True

    def mark_injected(self, request_id: int) -> bool:
        return self._post_ok("POST", f"/api/bridge/requests/{request_id}/injected")

    def claim_delivery(self, request_id: int) -> bool:
        return self._post_ok("POST", f"/api/bridge/requests/{request_id}/delivered")

    def release_delivery(self, request_id: int) -> None:
        self._post_ok("DELETE", f"/api/bridge/requests/{request_id}/delivered")

    def session_for(self, agent_id: str) -> Optional[SessionSpec]:
        if time.monotonic() - self._sessions_at > self.AGENTS_CACHE_SECONDS:
            resp = self.client.get("/api/agents")
            resp.raise_for_status()
            self._sessions = {
                agent_id: SessionSpec(
                    spec["tmux_session"],
                    spec.get("idle_patterns", []),
                    spec.get("busy_patterns", []),
                )
                for agent_id, spec in resp.json()["agents"].items()
            }
            self._sessions_at = time.monotonic()
        return self._sessions.get(agent_id)
