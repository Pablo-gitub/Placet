import sqlite3
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple

from pathlib import Path
from backend.app.models import (
    OPEN_STATUSES,
    RequestCreate,
    RequestEvent,
    RequestModel,
    RequestStatus,
    Priority,
    QuestionType,
    ResponseModel,
    is_settled,
)

# Allowed state transitions for update_request_decision (source -> targets).
# Delivery of agent-to-agent prompts has its own methods (claim_injection,
# confirm_injection, ...).
_DECISION_TRANSITIONS = {
    RequestStatus.PENDING: {RequestStatus.APPROVED, RequestStatus.REJECTED},
}

# Statuses in which the recipient may still reply.
_REPLYABLE = (RequestStatus.APPROVED, RequestStatus.DELIVERING, RequestStatus.EXECUTING)


def _ts(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


class Database:
    def __init__(self, db_path: str = "placet.db"):
        self.db_path = db_path

    @contextmanager
    def _get_connection(self) -> Iterator[sqlite3.Connection]:
        # Backend and bridge share this file from separate processes:
        # wait on locks instead of failing, and always close the handle.
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 10000;")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def init_db(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA journal_mode = WAL;")
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_id TEXT NOT NULL,
                recipient_id TEXT NOT NULL,
                question_type TEXT NOT NULL,
                prompt TEXT NOT NULL,
                context TEXT,
                options TEXT,
                status TEXT NOT NULL,
                edited_prompt TEXT,
                selected_options TEXT,
                custom_text TEXT,
                created_at TEXT NOT NULL,
                expires_at TEXT,
                decided_at TEXT,
                completed_at TEXT,
                updated_at TEXT,
                delivered_at TEXT,
                priority TEXT,
                delivery_attempts INTEGER NOT NULL DEFAULT 0,
                delivering_since TEXT,
                executing_since TEXT
            );
            """)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS responses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id INTEGER NOT NULL UNIQUE,
                responder_id TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                implicit INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (request_id) REFERENCES requests(id)
            );
            """)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS agent_states (
                agent_id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                turn_request_id INTEGER,
                updated_at TEXT NOT NULL
            );
            """)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS request_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id INTEGER NOT NULL,
                at TEXT NOT NULL,
                actor TEXT NOT NULL,
                event TEXT NOT NULL,
                details TEXT,
                FOREIGN KEY (request_id) REFERENCES requests(id)
            );
            """)
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_request_events_request ON request_events(request_id);"
            )
            cursor.execute("PRAGMA table_info(requests);")
            cols = [r["name"] for r in cursor.fetchall()]
            for col in ("expires_at", "updated_at", "delivered_at", "priority",
                        "delivering_since", "executing_since"):
                if col not in cols:
                    cursor.execute(f"ALTER TABLE requests ADD COLUMN {col} TEXT;")
            if "delivery_attempts" not in cols:
                cursor.execute(
                    "ALTER TABLE requests ADD COLUMN delivery_attempts INTEGER NOT NULL DEFAULT 0;"
                )
            cursor.execute("PRAGMA table_info(responses);")
            if "implicit" not in [r["name"] for r in cursor.fetchall()]:
                cursor.execute("ALTER TABLE responses ADD COLUMN implicit INTEGER NOT NULL DEFAULT 0;")

    @staticmethod
    def _log(
        cursor: sqlite3.Cursor,
        request_id: int,
        actor: str,
        event: str,
        details: Optional[Dict[str, Any]] = None,
        at: Optional[str] = None,
    ) -> None:
        """Appends to the audit log, in the same transaction as the change."""
        cursor.execute(
            "INSERT INTO request_events (request_id, at, actor, event, details) VALUES (?, ?, ?, ?, ?)",
            (
                request_id,
                at or datetime.now(timezone.utc).isoformat(),
                actor,
                event,
                json.dumps(details, ensure_ascii=False) if details else None,
            ),
        )

    def list_events(self, request_id: int) -> List[RequestEvent]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM request_events WHERE request_id = ? ORDER BY id ASC", (request_id,)
            )
            return [
                RequestEvent(
                    id=r["id"],
                    request_id=r["request_id"],
                    at=datetime.fromisoformat(r["at"]),
                    actor=r["actor"],
                    event=r["event"],
                    details=json.loads(r["details"]) if r["details"] else {},
                )
                for r in cursor.fetchall()
            ]

    def create_request(self, req: RequestCreate, actor: Optional[str] = None) -> RequestModel:
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=req.expires_in_seconds)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO requests (
                    sender_id, recipient_id, question_type, prompt, context,
                    options, status, created_at, expires_at, updated_at, priority
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    req.sender_id,
                    req.recipient_id,
                    req.question_type.value,
                    req.prompt,
                    req.context,
                    json.dumps(req.options),
                    RequestStatus.PENDING.value,
                    now.isoformat(),
                    expires_at.isoformat(),
                    now.isoformat(),
                    (req.priority or Priority.NORMAL).value,
                ),
            )
            req_id = cursor.lastrowid
            self._log(
                cursor, req_id, actor or f"agent:{req.sender_id}", "created",
                {"question_type": req.question_type.value, "expires_at": expires_at.isoformat()},
                at=now.isoformat(),
            )
        return self.get_request(req_id)

    def get_request(self, request_id: int) -> Optional[RequestModel]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM requests WHERE id = ?", (request_id,))
            row = cursor.fetchone()
            if not row:
                return None

            cursor.execute("SELECT * FROM responses WHERE request_id = ?", (request_id,))
            return self._row_to_model(row, cursor.fetchone())

    @staticmethod
    def _row_to_model(row: sqlite3.Row, resp_row: Optional[sqlite3.Row]) -> RequestModel:
        resp_model = None
        if resp_row:
            resp_model = ResponseModel(
                id=resp_row["id"],
                request_id=resp_row["request_id"],
                responder_id=resp_row["responder_id"],
                content=resp_row["content"],
                created_at=datetime.fromisoformat(resp_row["created_at"]),
                implicit=bool(resp_row["implicit"]),
            )

        return RequestModel(
            id=row["id"],
            sender_id=row["sender_id"],
            recipient_id=row["recipient_id"],
            question_type=QuestionType(row["question_type"]),
            prompt=row["prompt"],
            context=row["context"],
            options=json.loads(row["options"]) if row["options"] else [],
            status=RequestStatus(row["status"]),
            priority=Priority(row["priority"]) if row["priority"] else Priority.NORMAL,
            edited_prompt=row["edited_prompt"],
            selected_options=json.loads(row["selected_options"]) if row["selected_options"] else [],
            custom_text=row["custom_text"],
            created_at=datetime.fromisoformat(row["created_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None,
            decided_at=datetime.fromisoformat(row["decided_at"]) if row["decided_at"] else None,
            completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
            updated_at=datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else None,
            delivered_at=datetime.fromisoformat(row["delivered_at"]) if row["delivered_at"] else None,
            delivery_attempts=row["delivery_attempts"] or 0,
            delivering_since=_ts(row["delivering_since"]),
            executing_since=_ts(row["executing_since"]),
            response=resp_model,
        )

    def list_requests(
        self,
        status: Optional[RequestStatus] = None,
        limit: int = 50,
        oldest_first: bool = False,
    ) -> List[RequestModel]:
        """
        Newest first by default (what a UI wants); the bridge asks for
        oldest_first to process its queue in arrival order.
        """
        order = "ASC" if oldest_first else "DESC"
        where = "WHERE r.status = ?" if status else ""
        params = ([status.value] if status else []) + [limit]
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT r.*,
                       s.id AS s_id, s.request_id AS s_request_id,
                       s.responder_id AS s_responder_id, s.content AS s_content,
                       s.created_at AS s_created_at, s.implicit AS s_implicit
                FROM requests r
                LEFT JOIN responses s ON s.request_id = r.id
                {where}
                ORDER BY r.id {order}
                LIMIT ?
                """,
                params,
            )
            results = []
            for row in cursor.fetchall():
                resp = None
                if row["s_id"] is not None:
                    resp = {
                        "id": row["s_id"],
                        "request_id": row["s_request_id"],
                        "responder_id": row["s_responder_id"],
                        "content": row["s_content"],
                        "created_at": row["s_created_at"],
                        "implicit": row["s_implicit"],
                    }
                results.append(self._row_to_model(row, resp))
            return results

    def update_request_decision(
        self,
        request_id: int,
        status: RequestStatus,
        edited_prompt: Optional[str] = None,
        selected_options: Optional[List[str]] = None,
        custom_text: Optional[str] = None,
        actor: str = "system",
    ) -> Optional[RequestModel]:
        """
        Applies the human decision (PENDING -> APPROVED/REJECTED). Returns
        None if the request does not exist or is no longer PENDING.
        """
        allowed_sources = [
            src.value for src, targets in _DECISION_TRANSITIONS.items() if status in targets
        ]
        if not allowed_sources:
            return None

        now = datetime.now(timezone.utc).isoformat()
        placeholders = ",".join("?" for _ in allowed_sources)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                UPDATE requests
                SET status = ?, edited_prompt = ?, selected_options = ?, custom_text = ?,
                    decided_at = ?, updated_at = ?
                WHERE id = ? AND status IN ({placeholders})
                """,
                [
                    status.value,
                    edited_prompt,
                    json.dumps(selected_options) if selected_options is not None else None,
                    custom_text,
                    now,
                    now,
                    request_id,
                    *allowed_sources,
                ],
            )
            if cursor.rowcount == 0:
                return None
            details: Dict[str, Any] = {"status": status.value}
            if edited_prompt is not None:
                details["edited_prompt"] = edited_prompt
            if selected_options:
                details["selected_options"] = selected_options
            if custom_text:
                details["custom_text"] = custom_text
            self._log(cursor, request_id, actor, "decided", details, at=now)
        return self.get_request(request_id)

    def record_reply(
        self,
        request_id: int,
        responder_id: str,
        content: str,
        actor: Optional[str] = None,
        implicit: bool = False,
    ) -> Optional[RequestModel]:
        """
        Completes a request with the recipient's reply. A reply while still
        DELIVERING also proves the prompt arrived. Returns None if the
        request can no longer be replied to.
        """
        now = datetime.now(timezone.utc).isoformat()
        sources = [st.value for st in _REPLYABLE]
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                UPDATE requests SET status = ?, completed_at = ?, updated_at = ?
                WHERE id = ? AND status IN ({",".join("?" for _ in sources)})
                """,
                [RequestStatus.COMPLETED.value, now, now, request_id, *sources],
            )
            if cursor.rowcount == 0:
                return None
            cursor.execute(
                """
                INSERT OR REPLACE INTO responses (request_id, responder_id, content, created_at, implicit)
                VALUES (?, ?, ?, ?, ?)
                """,
                (request_id, responder_id, content, now, int(implicit)),
            )
            details: Dict[str, Any] = {"length": len(content)}
            if implicit:
                details["implicit"] = True
            self._log(cursor, request_id, actor or f"agent:{responder_id}", "replied", details, at=now)
        return self.get_request(request_id)

    # --- Injection delivery (agent-to-agent prompts) -------------------------

    def _transition(
        self,
        cursor: sqlite3.Cursor,
        request_id: int,
        source: RequestStatus,
        target: RequestStatus,
        now: str,
        extra_sql: str = "",
        extra_params: Sequence[Any] = (),
    ) -> bool:
        cursor.execute(
            f"UPDATE requests SET status = ?, updated_at = ?{extra_sql} WHERE id = ? AND status = ?",
            [target.value, now, *extra_params, request_id, source.value],
        )
        return cursor.rowcount > 0

    def claim_injection(self, request_id: int, actor: str = "bridge") -> Optional[RequestModel]:
        """APPROVED -> DELIVERING, before the prompt is sent: a crash after
        sending can then not cause a silent second injection."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if not self._transition(
                cursor, request_id, RequestStatus.APPROVED, RequestStatus.DELIVERING, now,
                ", delivering_since = ?, delivery_attempts = delivery_attempts + 1", [now],
            ):
                return None
            cursor.execute("SELECT delivery_attempts FROM requests WHERE id = ?", (request_id,))
            attempt = cursor.fetchone()["delivery_attempts"]
            self._log(cursor, request_id, actor, "injection_claimed", {"attempt": attempt}, at=now)
        return self.get_request(request_id)

    def release_injection(self, request_id: int, actor: str = "bridge") -> bool:
        """DELIVERING -> APPROVED when sending failed: nothing reached the
        agent, so the attempt does not count."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if not self._transition(
                cursor, request_id, RequestStatus.DELIVERING, RequestStatus.APPROVED, now,
                ", delivering_since = NULL, delivery_attempts = MAX(delivery_attempts - 1, 0)",
            ):
                return False
            self._log(cursor, request_id, actor, "injection_released", at=now)
            return True

    def confirm_injection(self, request_id: int, actor: str, via: str) -> Optional[RequestModel]:
        """DELIVERING -> EXECUTING: the agent (or, without acks, the bridge)
        confirmed the prompt arrived."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if not self._transition(
                cursor, request_id, RequestStatus.DELIVERING, RequestStatus.EXECUTING, now,
                ", executing_since = ?", [now],
            ):
                return None
            self._log(cursor, request_id, actor, "acked", {"via": via}, at=now)
        return self.get_request(request_id)

    def requeue_unacked(
        self, ack_timeout_seconds: float, max_attempts: int
    ) -> Tuple[List[int], List[int]]:
        """
        Injections without an ack in time go back to APPROVED to be retried,
        or to DELIVERY_FAILED after max_attempts. Returns (requeued, failed).
        """
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        cutoff = (now_dt - timedelta(seconds=ack_timeout_seconds)).isoformat()
        requeued, failed = [], []
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, delivery_attempts FROM requests WHERE status = ? AND delivering_since <= ?",
                (RequestStatus.DELIVERING.value, cutoff),
            )
            for row in cursor.fetchall():
                if row["delivery_attempts"] >= max_attempts:
                    if self._transition(cursor, row["id"], RequestStatus.DELIVERING,
                                        RequestStatus.DELIVERY_FAILED, now):
                        self._log(cursor, row["id"], "system", "injection_failed",
                                  {"attempts": row["delivery_attempts"]}, at=now)
                        failed.append(row["id"])
                elif self._transition(cursor, row["id"], RequestStatus.DELIVERING,
                                      RequestStatus.APPROVED, now, ", delivering_since = NULL"):
                    self._log(cursor, row["id"], "system", "injection_requeued",
                              {"attempts": row["delivery_attempts"]}, at=now)
                    requeued.append(row["id"])
        return requeued, failed

    def stall_executing(
        self, timeout_for: Callable[[str], float]
    ) -> List[int]:
        """EXECUTING longer than the recipient's execution timeout -> STALLED."""
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        stalled = []
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, recipient_id, executing_since FROM requests WHERE status = ?",
                (RequestStatus.EXECUTING.value,),
            )
            for row in cursor.fetchall():
                since = _ts(row["executing_since"])
                if since is None or (now_dt - since).total_seconds() < timeout_for(row["recipient_id"]):
                    continue
                if self._transition(cursor, row["id"], RequestStatus.EXECUTING,
                                    RequestStatus.STALLED, now):
                    self._log(cursor, row["id"], "system", "stalled", at=now)
                    stalled.append(row["id"])
        return stalled

    def recipients_with_delivery_in_flight(self) -> List[str]:
        """Agents with a DELIVERING request: one injection in flight per session."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT DISTINCT recipient_id FROM requests WHERE status = ?",
                (RequestStatus.DELIVERING.value,),
            )
            return [r["recipient_id"] for r in cursor.fetchall()]

    def injections_ready(self) -> List[RequestModel]:
        """
        APPROVED agent-to-agent requests to inject, oldest first, at most one
        per recipient and none for a recipient with a delivery in flight.
        """
        in_flight = set(self.recipients_with_delivery_in_flight())
        ready = []
        for r in self.list_requests(status=RequestStatus.APPROVED, oldest_first=True, limit=200):
            if r.recipient_id == "human" or r.recipient_id in in_flight:
                continue
            ready.append(r)
            in_flight.add(r.recipient_id)
        return ready

    # --- Agent turn state (from CLI hooks) -----------------------------------

    def busy_agents(self, ttl_seconds: float) -> List[str]:
        """Agents whose hooks declared a turn in progress within the TTL."""
        since = (datetime.now(timezone.utc) - timedelta(seconds=ttl_seconds)).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT agent_id FROM agent_states WHERE state = 'busy' AND updated_at > ?",
                (since,),
            )
            return [r["agent_id"] for r in cursor.fetchall()]


    def set_agent_state(
        self, agent_id: str, state: str, turn_request_id: Optional[int] = None
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO agent_states (agent_id, state, turn_request_id, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    state = excluded.state,
                    turn_request_id = excluded.turn_request_id,
                    updated_at = excluded.updated_at
                """,
                (agent_id, state, turn_request_id, now),
            )

    def get_agent_states(self) -> Dict[str, Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM agent_states")
            return {
                r["agent_id"]: {
                    "state": r["state"],
                    "turn_request_id": r["turn_request_id"],
                    "updated_at": r["updated_at"],
                }
                for r in cursor.fetchall()
            }

    def cancel_request(
        self, request_id: int, by_sender: bool = False, actor: str = "system"
    ) -> bool:
        """
        Cancels a PENDING request. When the sender itself cancels, it already
        knows the outcome, so the result is marked as delivered.
        """
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE requests SET status = ?, updated_at = ?, delivered_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    RequestStatus.CANCELLED.value,
                    now,
                    now if by_sender else None,
                    request_id,
                    RequestStatus.PENDING.value,
                ),
            )
            if cursor.rowcount == 0:
                return False
            self._log(cursor, request_id, actor, "cancelled", at=now)
            return True

    def expire_stale_requests(self) -> List[int]:
        """
        Marks as EXPIRED every open request (PENDING, APPROVED, EXECUTING) whose
        expires_at is in the past. Returns the ids that changed.
        """
        now = datetime.now(timezone.utc).isoformat()
        open_values = [s.value for s in OPEN_STATUSES]
        placeholders = ",".join("?" for _ in open_values)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # ISO-8601 UTC timestamps with the same offset compare correctly as text.
            cursor.execute(
                f"""
                UPDATE requests SET status = ?, updated_at = ?
                WHERE status IN ({placeholders}) AND expires_at IS NOT NULL AND expires_at <= ?
                RETURNING id
                """,
                [RequestStatus.EXPIRED.value, now, *open_values, now],
            )
            expired = sorted(r["id"] for r in cursor.fetchall())
            for req_id in expired:
                self._log(cursor, req_id, "system", "expired", at=now)
            return expired

    def mark_delivered(self, request_id: int, actor: str = "system", via: str = "") -> bool:
        """Records that the outcome reached the sender; True only the first time."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE requests SET delivered_at = ? WHERE id = ? AND delivered_at IS NULL",
                (now, request_id),
            )
            if cursor.rowcount == 0:
                return False
            self._log(cursor, request_id, actor, "delivered", {"via": via} if via else None, at=now)
            return True

    def clear_delivered(self, request_id: int, actor: str = "system") -> None:
        """Undoes mark_delivered after a failed delivery, so it is retried."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE requests SET delivered_at = NULL WHERE id = ? AND delivered_at IS NOT NULL",
                (request_id,),
            )
            if cursor.rowcount:
                self._log(cursor, request_id, actor, "delivery_failed")

    def list_undelivered_results(
        self, settled_before: datetime, settled_after: datetime, limit: int = 50
    ) -> List[RequestModel]:
        """
        Settled requests whose outcome has not reached the sender yet, oldest
        first, whose last change falls in (settled_after, settled_before].
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id FROM requests
                WHERE delivered_at IS NULL AND status != ?
                  AND updated_at IS NOT NULL AND updated_at <= ? AND updated_at > ?
                ORDER BY updated_at ASC
                LIMIT ?
                """,
                (
                    RequestStatus.PENDING.value,
                    settled_before.isoformat(),
                    settled_after.isoformat(),
                    limit,
                ),
            )
            ids = [r["id"] for r in cursor.fetchall()]
        requests = (self.get_request(i) for i in ids)
        return [r for r in requests if r is not None and is_settled(r)]
