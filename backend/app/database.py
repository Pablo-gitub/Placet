import sqlite3
import json
from datetime import datetime, timezone
from typing import List, Optional

from pathlib import Path
from backend.app.models import (
    RequestCreate,
    RequestModel,
    RequestStatus,
    QuestionType,
    ResponseModel,
)


class Database:
    def __init__(self, db_path: str = "placet.db"):
        self.db_path = db_path

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._get_connection() as conn:
            cursor = conn.cursor()
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
                timeout_seconds INTEGER DEFAULT 180,
                created_at TEXT NOT NULL,
                decided_at TEXT,
                completed_at TEXT
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
            cursor.execute("PRAGMA table_info(requests);")
            cols = [r["name"] for r in cursor.fetchall()]
            if "timeout_seconds" not in cols:
                cursor.execute("ALTER TABLE requests ADD COLUMN timeout_seconds INTEGER DEFAULT 180;")
            conn.commit()


    def create_request(self, req: RequestCreate) -> RequestModel:
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO requests (
                    sender_id, recipient_id, question_type, prompt, context,
                    options, status, timeout_seconds, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    req.sender_id,
                    req.recipient_id,
                    req.question_type.value,
                    req.prompt,
                    req.context,
                    json.dumps(req.options),
                    RequestStatus.PENDING.value,
                    req.timeout_seconds,
                    now,
                ),
            )
            req_id = cursor.lastrowid
            conn.commit()
            return self.get_request(req_id)

    def get_request(self, request_id: int) -> Optional[RequestModel]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM requests WHERE id = ?", (request_id,))
            row = cursor.fetchone()
            if not row:
                return None

            cursor.execute("SELECT * FROM responses WHERE request_id = ?", (request_id,))
            resp_row = cursor.fetchone()
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
                timeout_seconds=row["timeout_seconds"] if "timeout_seconds" in row.keys() and row["timeout_seconds"] is not None else 180,
                created_at=datetime.fromisoformat(row["created_at"]),
                decided_at=datetime.fromisoformat(row["decided_at"]) if row["decided_at"] else None,
                completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
                response=resp_model,
            )


    def list_requests(
        self, status: Optional[RequestStatus] = None, limit: int = 50
    ) -> List[RequestModel]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if status:
                cursor.execute(
                    "SELECT id FROM requests WHERE status = ? ORDER BY id DESC LIMIT ?",
                    (status.value, limit),
                )
            else:
                cursor.execute(
                    "SELECT id FROM requests ORDER BY id DESC LIMIT ?", (limit,)
                )
            rows = cursor.fetchall()
            return [self.get_request(r["id"]) for r in rows if r["id"]]

    def update_request_decision(
        self,
        request_id: int,
        status: RequestStatus,
        edited_prompt: Optional[str] = None,
        selected_options: Optional[List[str]] = None,
        custom_text: Optional[str] = None,
    ) -> Optional[RequestModel]:
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM requests WHERE id = ?", (request_id,))
            row = cursor.fetchone()
            if not row:
                return None

            curr_status = row["status"]
            # Guard: Only allow decision if currently PENDING, OR if bridge is transitioning APPROVED -> EXECUTING
            is_valid_transition = (
                curr_status == RequestStatus.PENDING.value or
                (curr_status == RequestStatus.APPROVED.value and status == RequestStatus.EXECUTING)
            )
            if not is_valid_transition:
                return None

            cursor.execute(
                """
                UPDATE requests
                SET status = ?, edited_prompt = ?, selected_options = ?, custom_text = ?, decided_at = ?
                WHERE id = ?
                """,
                (
                    status.value,
                    edited_prompt,
                    json.dumps(selected_options) if selected_options is not None else None,
                    custom_text,
                    now,
                    request_id,
                ),
            )
            conn.commit()
            return self.get_request(request_id)

    def record_reply(
        self, request_id: int, responder_id: str, content: str
    ) -> Optional[RequestModel]:
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM requests WHERE id = ?", (request_id,))
            row = cursor.fetchone()
            if not row:
                return None

            curr_status = row["status"]
            # Guard: only allow reply if request is APPROVED or EXECUTING
            if curr_status not in (RequestStatus.APPROVED.value, RequestStatus.EXECUTING.value):
                return None

            cursor.execute(
                """
                INSERT OR REPLACE INTO responses (request_id, responder_id, content, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (request_id, responder_id, content, now),
            )
            cursor.execute(
                """
                UPDATE requests
                SET status = ?, completed_at = ?
                WHERE id = ?
                """,
                (RequestStatus.COMPLETED.value, now, request_id),
            )
            conn.commit()
            return self.get_request(request_id)

    def cancel_request(self, request_id: int) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE requests SET status = ? WHERE id = ? AND status = ?",
                (RequestStatus.CANCELLED.value, request_id, RequestStatus.PENDING.value),
            )
            conn.commit()
            return cursor.rowcount > 0

    def expire_stale_requests(self) -> int:
        now_dt = datetime.now(timezone.utc)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, created_at, timeout_seconds FROM requests WHERE status = ?",
                (RequestStatus.PENDING.value,),
            )
            rows = cursor.fetchall()
            expired_ids = []
            for r in rows:
                created_at = datetime.fromisoformat(r["created_at"])
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
                timeout_sec = r["timeout_seconds"] if ("timeout_seconds" in r.keys() and r["timeout_seconds"] is not None) else 180
                if (now_dt - created_at).total_seconds() >= timeout_sec:
                    expired_ids.append(r["id"])

            if expired_ids:
                placeholders = ",".join("?" for _ in expired_ids)
                cursor.execute(
                    f"UPDATE requests SET status = ? WHERE id IN ({placeholders})",
                    [RequestStatus.EXPIRED.value] + expired_ids,
                )
                conn.commit()
            return len(expired_ids)

