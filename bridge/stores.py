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
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Protocol

import httpx

from backend.app.config import PlacetConfig, load_agents_config
from backend.app.database import Database
from backend.app.models import (
    AGENT_BUSY_TTL_SECONDS,
    RESULT_GRACE_SECONDS,
    RESULT_MAX_AGE_SECONDS,
    RequestModel,
)
from bridge.tmux_controller import SessionSpec

logger = logging.getLogger(__name__)

BRIDGE_ACTOR = "bridge"
# How long one poll for jobs (conversation pages) waits on the backend.
JOBS_WAIT_SECONDS = 25


@dataclass
class Work:
    # APPROVED agent-to-agent requests, at most one per recipient.
    injections: List[RequestModel] = field(default_factory=list)
    # Settled outcomes to deliver to the sender's session.
    results: List[RequestModel] = field(default_factory=list)
    # Agents whose hooks declared a turn in progress.
    busy_agents: List[str] = field(default_factory=list)


class BridgeStore(Protocol):
    def work(self) -> Work: ...
    # Injections: claim before sending, release if sending failed, confirm
    # only for agents without acknowledgements (ack_mode none).
    def claim_injection(self, request_id: int) -> bool: ...
    def release_injection(self, request_id: int) -> None: ...
    def confirm_injection(self, request_id: int) -> bool: ...
    # Outcome delivery to the sender.
    def claim_delivery(self, request_id: int) -> bool: ...
    def release_delivery(self, request_id: int) -> None: ...
    def session_for(self, agent_id: str) -> Optional[SessionSpec]: ...


def _session_spec(spec) -> SessionSpec:
    return SessionSpec(
        name=spec.tmux_session,
        idle_patterns=list(spec.idle_patterns),
        busy_patterns=list(spec.busy_patterns),
        ack_mode=spec.ack_mode,
    )


class LocalStore:
    def __init__(self, db: Optional[Database] = None, config: Optional[PlacetConfig] = None):
        self.db = db or Database(os.environ.get("PLACET_DB_PATH", "placet.db"))
        self.config = config or load_agents_config()

    def work(self) -> Work:
        now = datetime.now(timezone.utc)
        injections, results, busy = self.db.tmux_work_filter(
            self.db.injections_ready(),
            self.db.list_undelivered_results(
                settled_before=now - timedelta(seconds=RESULT_GRACE_SECONDS),
                settled_after=now - timedelta(seconds=RESULT_MAX_AGE_SECONDS),
            ),
            lambda agent_id: getattr(self.config.get_agent(agent_id), "tmux_session", None),
        )
        return Work(
            injections=injections,
            results=results,
            busy_agents=sorted(set(self.db.busy_agents(AGENT_BUSY_TTL_SECONDS)) | set(busy)),
        )

    def claim_injection(self, request_id: int) -> bool:
        return self.db.claim_injection(request_id, actor=BRIDGE_ACTOR) is not None

    def release_injection(self, request_id: int) -> None:
        self.db.release_injection(request_id, actor=BRIDGE_ACTOR)

    def confirm_injection(self, request_id: int) -> bool:
        return self.db.confirm_injection(request_id, actor=BRIDGE_ACTOR, via="bridge") is not None

    def claim_delivery(self, request_id: int) -> bool:
        return self.db.mark_delivered(request_id, actor=BRIDGE_ACTOR, via="tmux")

    def release_delivery(self, request_id: int) -> None:
        self.db.clear_delivered(request_id, actor=BRIDGE_ACTOR)

    def session_for(self, agent_id: str) -> Optional[SessionSpec]:
        agent = self.config.get_agent(agent_id)
        return _session_spec(agent) if agent else None


class HttpStore:
    AGENTS_CACHE_SECONDS = 60.0

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        client: Optional[httpx.Client] = None,
        jobs_client: Optional[httpx.Client] = None,
    ):
        base_url = (base_url or os.environ.get("PLACET_API_URL", "http://127.0.0.1:8000")).rstrip("/")
        token = token if token is not None else os.environ.get("PLACET_BRIDGE_TOKEN", "")
        if not token:
            raise SystemExit("PLACET_BRIDGE_TOKEN is not set: the bridge cannot authenticate.")
        self.client = client or httpx.Client(timeout=10.0)
        self.client.base_url = base_url
        self.client.headers["Authorization"] = f"Bearer {token}"
        # Jobs are long-polled from another thread: a client of their own.
        self.jobs_client = jobs_client or httpx.Client(timeout=JOBS_WAIT_SECONDS + 15.0)
        self.jobs_client.base_url = base_url
        self.jobs_client.headers["Authorization"] = f"Bearer {token}"
        self._sessions: Dict[str, SessionSpec] = {}
        self._sessions_at = 0.0

    def work(self) -> Work:
        resp = self.client.get("/api/bridge/work")
        resp.raise_for_status()
        data = resp.json()
        return Work(
            injections=[RequestModel(**r) for r in data["injections"]],
            results=[RequestModel(**r) for r in data["results"]],
            busy_agents=list(data.get("busy_agents", [])),
        )

    def _call_ok(self, method: str, path: str) -> bool:
        resp = self.client.request(method, path)
        if resp.status_code == 409:
            return False
        resp.raise_for_status()
        return True

    def claim_injection(self, request_id: int) -> bool:
        return self._call_ok("POST", f"/api/bridge/requests/{request_id}/claim")

    def release_injection(self, request_id: int) -> None:
        self._call_ok("DELETE", f"/api/bridge/requests/{request_id}/claim")

    def confirm_injection(self, request_id: int) -> bool:
        return self._call_ok("POST", f"/api/bridge/requests/{request_id}/injected")

    def claim_delivery(self, request_id: int) -> bool:
        return self._call_ok("POST", f"/api/bridge/requests/{request_id}/delivered")

    def release_delivery(self, request_id: int) -> None:
        self._call_ok("DELETE", f"/api/bridge/requests/{request_id}/delivered")

    def session_for(self, agent_id: str) -> Optional[SessionSpec]:
        if time.monotonic() - self._sessions_at > self.AGENTS_CACHE_SECONDS:
            resp = self.client.get("/api/agents")
            resp.raise_for_status()
            self._sessions = {
                agent_id: SessionSpec(
                    name=spec["tmux_session"],
                    idle_patterns=spec.get("idle_patterns", []),
                    busy_patterns=spec.get("busy_patterns", []),
                    ack_mode=spec.get("ack_mode", "mcp"),
                )
                for agent_id, spec in resp.json()["agents"].items()
            }
            self._sessions_at = time.monotonic()
        return self._sessions.get(agent_id)

    # --- Jobs someone is waiting for (conversation pages) ---------------------

    def take_jobs(self, wait_seconds: int = JOBS_WAIT_SECONDS) -> List[dict]:
        resp = self.jobs_client.get("/api/bridge/jobs", params={"wait_seconds": wait_seconds})
        if resp.status_code == 204:
            return []
        resp.raise_for_status()
        return list(resp.json().get("jobs", []))

    def post_job_result(self, job_id: str, result: dict) -> None:
        resp = self.jobs_client.post(f"/api/bridge/jobs/{job_id}/result", json=result)
        resp.raise_for_status()
