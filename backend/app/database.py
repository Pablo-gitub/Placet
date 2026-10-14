import sqlite3
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterator, List, Optional

from pathlib import Path
from backend.app.models import (
    OPEN_STATUSES,
    RequestCreate,
    RequestEvent,
    RequestModel,
    RequestStatus,
    QuestionType,
    ResponseModel,
    is_settled,
)

# Allowed state transitions for update_request_decision (source -> targets).
_DECISION_TRANSITIONS = {
    RequestStatus.PENDING: {RequestStatus.APPROVED, RequestStatus.REJECTED},
    RequestStatus.APPROVED: {RequestStatus.EXECUTING},
}


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
                delivered_at TEXT
            );
            """)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS responses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id INTEGER NOT NULL UNIQUE,
                responder_id TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (request_id) REFERENCES requests(id)
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
            for col in ("expires_at", "updated_at", "delivered_at"):
                if col not in cols:
                    cursor.execute(f"ALTER TABLE requests ADD COLUMN {col} TEXT;")

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
                    options, status, created_at, expires_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            edited_prompt=row["edited_prompt"],
            selected_options=json.loads(row["selected_options"]) if row["selected_options"] else [],
            custom_text=row["custom_text"],
            created_at=datetime.fromisoformat(row["created_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None,
            decided_at=datetime.fromisoformat(row["decided_at"]) if row["decided_at"] else None,
            completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
            updated_at=datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else None,
            delivered_at=datetime.fromisoformat(row["delivered_at"]) if row["delivered_at"] else None,
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
                       s.created_at AS s_created_at
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
        Applies a decision (PENDING -> APPROVED/REJECTED) or the bridge hand-off
        (APPROVED -> EXECUTING). Returns None if the request does not exist or
        the transition is not allowed from its current state.
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
            if status == RequestStatus.EXECUTING:
                # Hand-off keeps the human decision fields untouched.
                cursor.execute(
                    f"UPDATE requests SET status = ?, updated_at = ? WHERE id = ? AND status IN ({placeholders})",
                    [status.value, now, request_id, *allowed_sources],
                )
            else:
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
            if status == RequestStatus.EXECUTING:
                self._log(cursor, request_id, actor, "injected", at=now)
            else:
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
        self, request_id: int, responder_id: str, content: str, actor: Optional[str] = None
    ) -> Optional[RequestModel]:
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Guard: only allow reply if request is APPROVED or EXECUTING
            cursor.execute(
                "UPDATE requests SET status = ?, completed_at = ?, updated_at = ? WHERE id = ? AND status IN (?, ?)",
                (
                    RequestStatus.COMPLETED.value,
                    now,
                    now,
                    request_id,
                    RequestStatus.APPROVED.value,
                    RequestStatus.EXECUTING.value,
                ),
            )
            if cursor.rowcount == 0:
                return None
            cursor.execute(
                """
                INSERT OR REPLACE INTO responses (request_id, responder_id, content, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (request_id, responder_id, content, now),
            )
            self._log(cursor, request_id, actor or f"agent:{responder_id}", "replied",
                      {"length": len(content)}, at=now)
        return self.get_request(request_id)

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
