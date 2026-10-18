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

from backend.app import envelopes
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
    language: Optional[str] = None
    # APPROVED agent-to-agent requests, at most one per recipient.
    injections: List[RequestModel] = field(default_factory=list)
    # Settled outcomes to deliver to the sender's session.
    results: List[RequestModel] = field(default_factory=list)
    # Agents whose hooks declared a turn in progress.
    busy_agents: List[str] = field(default_factory=list)
    # Sessions the bridge wakes in their app (Codex, Antigravity) with a
    # message waiting: {"id", "agent_id", "provider"}.
    sessions: List[dict] = field(default_factory=list)
    # Uncertain deliveries to look for in the recipient's conversation:
    # {"request_id", "session_id", "agent_id", "provider"}.
    uncertain: List[dict] = field(default_factory=list)
    # Folders of known sessions, to look for `.placet-off` (RF-45).
    folders: List[str] = field(default_factory=list)
    # Codex and Antigravity sessions whose titles the bridge reads: {"id", "provider"}.
    titles: List[dict] = field(default_factory=list)
    # "Now" messages (RF-35) for running turns, and those whose turn ended
    # before they got in: {"request_id", "session_id", "provider"}.
    steer: List[dict] = field(default_factory=list)
    steer_waiting: List[dict] = field(default_factory=list)
    # Suspended Codex sessions: queue once, then request native opening.
    wake_sessions: List[dict] = field(default_factory=list)
    claude_wake_sessions: List[dict] = field(default_factory=list)


@dataclass
class SessionMail:
    kind: str          # "injection" or "outcome"
    request_id: int
    text: str


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
    # Sessions woken in their app: take the next message, then confirm the
    # injection or give the message back if it could not be queued.
    def take_session_mail(self, session_id: str) -> Optional[SessionMail]: ...
    # The send ran but its outcome is unknown; later found in the conversation.
    def mark_uncertain(self, request_id: int) -> None: ...
    def mark_seen(self, request_id: int) -> None: ...
    # Which of the folders have a `.placet-off`.
    def report_folders(self, checked: List[str], excluded: List[str]) -> None: ...
    def report_titles(self, titles: Dict[str, str]) -> None: ...
    def report_usage(self, providers: List[dict]) -> None: ...
    # Takes a "now" message: {"request_id", "session_id", "provider", "text"} or None.
    def take_steer(self, request_id: int) -> Optional[dict]: ...


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
            language=self.db.get_background_language(),
            claude_wake_sessions=[{"id": s.id, "cwd": s.cwd}
                                 for s in self.db.claude_sessions_to_wake()],
            wake_sessions=[{"id": s.id, "provider": s.provider, "suspended": True}
                           for s in self.db.bridge_sessions_with_mail(include_suspended=True)
                           if s.suspended_at is not None],
            injections=injections,
            results=results,
            busy_agents=sorted(set(self.db.busy_agents(AGENT_BUSY_TTL_SECONDS)) | set(busy)),
            sessions=[
                {"id": s.id, "agent_id": s.agent_id, "provider": s.provider}
                for s in self.db.bridge_sessions_with_mail()
            ],
            uncertain=[
                {"request_id": r.id, "session_id": s.id, "agent_id": s.agent_id, "provider": s.provider}
                for r in self.db.uncertain_deliveries()
                if (s := self.db.get_session(r.recipient_session_id)) is not None
            ],
            folders=self.db.known_folders(),
            titles=[{"id": s.id, "provider": s.provider} for s in self.db.untitled_bridge_sessions()],
            steer=[{"request_id": r.id, "session_id": s.id, "provider": s.provider}
                   for r, s in self.db.steer_candidates()],
            steer_waiting=[{"request_id": r.id, "session_id": s.id, "provider": s.provider}
                           for r, s in self.db.steers_waiting()],
        )

    def take_session_mail(self, session_id: str) -> Optional[SessionMail]:
        session = self.db.get_session(session_id)
        if session is None:
            return None
        taken = self.db.take_inbox_message(session, actor=BRIDGE_ACTOR, via="bridge")
        if taken is None:
            return None
        kind, req = taken
        text = (envelopes.outcome(req, channel="app", language=self.db.get_background_language()) if kind == "outcome"
                else envelopes.injection(req, ack_mode="none", channel="app", language=self.db.get_background_language()))
        return SessionMail(kind, req.id, text)

    def claim_injection(self, request_id: int) -> bool:
        return self.db.claim_injection(request_id, actor=BRIDGE_ACTOR) is not None

    def release_injection(self, request_id: int) -> None:
        self.db.release_injection(request_id, actor=BRIDGE_ACTOR)

    def confirm_injection(self, request_id: int) -> bool:
        return self.db.confirm_injection(request_id, actor=BRIDGE_ACTOR, via="bridge") is not None

    def mark_uncertain(self, request_id: int) -> None:
        self.db.mark_delivery_uncertain(request_id, actor=BRIDGE_ACTOR, reason="channel timeout")

    def mark_seen(self, request_id: int) -> None:
        self.db.confirm_injection(request_id, actor=BRIDGE_ACTOR, via="transcript")

    def report_folders(self, checked: List[str], excluded: List[str]) -> None:
        self.db.set_excluded_folders(checked, excluded, actor=BRIDGE_ACTOR)

    def report_titles(self, titles: Dict[str, str]) -> None:
        self.db.set_session_titles(titles)

    def report_usage(self, providers: List[dict]) -> None:
        from backend.app.models import UsageReport
        for item in UsageReport(providers=providers).providers:
            self.db.set_usage(item.provider, item.model_dump_json())

    def take_steer(self, request_id: int) -> Optional[dict]:
        req = self.db.get_request(request_id)
        session = self.db.get_session(req.recipient_session_id) if req and req.recipient_session_id else None
        if session is None or session.state != "busy":
            return None
        claimed = self.db.claim_steer(request_id, session.id, actor=BRIDGE_ACTOR)
        if claimed is None:
            return None
        return {"request_id": request_id, "session_id": session.id, "provider": session.provider,
                "text": envelopes.steer(claimed, language=self.db.get_background_language())}

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
            language=data.get("language"),
            claude_wake_sessions=list(data.get("claude_wake_sessions", [])),
            wake_sessions=list(data.get("wake_sessions", [])),
            injections=[RequestModel(**r) for r in data["injections"]],
            results=[RequestModel(**r) for r in data["results"]],
            busy_agents=list(data.get("busy_agents", [])),
            sessions=list(data.get("sessions", [])),
            uncertain=list(data.get("uncertain", [])),
            folders=list(data.get("folders", [])),
            titles=list(data.get("titles", [])),
            steer=list(data.get("steer", [])),
            steer_waiting=list(data.get("steer_waiting", [])),
        )

    def take_session_mail(self, session_id: str) -> Optional[SessionMail]:
        resp = self.client.post(f"/api/bridge/sessions/{session_id}/take")
        if resp.status_code in (204, 404):
            return None
        resp.raise_for_status()
        data = resp.json()
        return SessionMail(data["kind"], data["request_id"], data["text"])

    def _call_ok(self, method: str, path: str) -> bool:
        resp = self.client.request(method, path)
        if resp.status_code == 409:
            return False
        resp.raise_for_status()
        return True

    def claim_injection(self, request_id: int) -> bool:
        return self._call_ok("POST", f"/api/bridge/requests/{request_id}/claim")

    def mark_uncertain(self, request_id: int) -> None:
        self._call_ok("POST", f"/api/bridge/requests/{request_id}/uncertain")

    def mark_seen(self, request_id: int) -> None:
        self._call_ok("POST", f"/api/bridge/requests/{request_id}/seen")

    def report_titles(self, titles: Dict[str, str]) -> None:
        resp = self.client.post("/api/bridge/titles", json={"titles": titles})
        resp.raise_for_status()

    def report_usage(self, providers: List[dict]) -> None:
        resp = self.client.post("/api/bridge/usage", json={"providers": providers})
        resp.raise_for_status()

    def take_steer(self, request_id: int) -> Optional[dict]:
        resp = self.client.post(f"/api/bridge/requests/{request_id}/steer")
        if resp.status_code in (404, 409):
            return None
        resp.raise_for_status()
        return resp.json()

    def report_folders(self, checked: List[str], excluded: List[str]) -> None:
        resp = self.client.post("/api/bridge/folders", json={"checked": checked, "excluded": excluded})
        resp.raise_for_status()

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
