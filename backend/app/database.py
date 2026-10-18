import sqlite3
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Set, Tuple

import uuid
from pathlib import Path
from backend.app.models import (
    BRIDGE_PROVIDERS,
    STEER_PROVIDERS,
    START_UNCONFIRMED_SECONDS,
    OPEN_STATUSES,
    RequestCreate,
    RequestEvent,
    RequestModel,
    RequestStatus,
    Priority,
    QuestionType,
    ResponseModel,
    NodeModel,
    RelationModel,
    RelationTypeModel,
    SessionModel,
    WAITER_TTL_SECONDS,
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

# A delivery that has not reached the session yet: cancelled with its request.
_UNSETTLED_DELIVERY = "delivery_state IN ('waiting', 'in_progress', 'uncertain')"
_CANCEL_DELIVERY_SQL = (
    f", delivery_state = CASE WHEN delivery_state IS NULL OR {_UNSETTLED_DELIVERY}"
    " THEN 'cancelled' ELSE delivery_state END"
)

_BACKFILL_ASPECTS_SQL = """
    UPDATE requests SET
      delivery_state = CASE
        WHEN status = 'DELIVERING' THEN 'in_progress'
        WHEN status = 'DELIVERY_FAILED' THEN 'failed'
        WHEN executing_since IS NOT NULL THEN 'accepted'
        WHEN status IN ('REJECTED', 'CANCELLED', 'EXPIRED') THEN 'cancelled'
        ELSE 'waiting' END,
      delivery_at = executing_since,
      execution_state = CASE
        WHEN status IN ('EXECUTING', 'STALLED') THEN 'started'
        WHEN status = 'COMPLETED' AND executing_since IS NOT NULL THEN 'completed'
        ELSE 'unobserved' END,
      reply_state = CASE
        WHEN EXISTS (SELECT 1 FROM responses r WHERE r.request_id = requests.id AND r.implicit = 1)
          THEN 'implicit'
        WHEN EXISTS (SELECT 1 FROM responses r WHERE r.request_id = requests.id) THEN 'explicit'
        ELSE 'none' END
"""


# Requests that do not belong to a session of an excluded project (RF-45).
_NOT_EXCLUDED_SQL = (
    "NOT EXISTS (SELECT 1 FROM sessions x WHERE x.excluded_at IS NOT NULL"
    " AND x.id IN (r.sender_session_id, r.recipient_session_id))"
)


# Sessions with the name of their node (RFC-02).
SESSIONS_WITH_NODE = (
    "SELECT sessions.*, nodes.name AS node_name FROM sessions"
    " LEFT JOIN nodes ON nodes.id = sessions.node_id"
)
# `/clear` ends one conversation and starts the next within a few seconds.
REATTACH_SECONDS = 120.0


class NameTaken(ValueError):
    """Another node or relation type already has this name (names ignore case)."""


NodeNameTaken = NameTaken


def _new_attempt() -> str:
    return uuid.uuid4().hex[:8]


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

    def get_background_language(self) -> str:
        from backend.app.i18n import default_language
        with self._get_connection() as conn:
            row = conn.execute("SELECT value FROM app_settings WHERE key = 'background_language'").fetchone()
        return row[0] if row and row[0] in ("en", "it") else default_language()

    def set_background_language(self, language: str) -> None:
        if language not in ("en", "it"):
            raise ValueError("Unsupported background language")
        with self._get_connection() as conn:
            conn.execute("INSERT INTO app_settings (key, value) VALUES ('background_language', ?)"
                         " ON CONFLICT(key) DO UPDATE SET value = excluded.value", (language,))

    def get_relation_rule(self) -> str:
        """The general rule agents read before each automatic relation (RFC-06)."""
        with self._get_connection() as conn:
            row = conn.execute("SELECT value FROM app_settings WHERE key = 'relation_rule'").fetchone()
        return row[0] if row else ""

    def set_relation_rule(self, rule: str) -> None:
        with self._get_connection() as conn:
            conn.execute("INSERT INTO app_settings (key, value) VALUES ('relation_rule', ?)"
                         " ON CONFLICT(key) DO UPDATE SET value = excluded.value", (rule,))

    # --- Automation limits (RFC-14) -------------------------------------------

    AUTOMATION_DEFAULTS = {"automation_paused": "0", "max_auto_hops": "3", "max_auto_per_hour": "20"}

    def get_automation(self) -> Dict[str, Any]:
        with self._get_connection() as conn:
            rows = dict(conn.execute(
                "SELECT key, value FROM app_settings WHERE key IN (?, ?, ?)",
                tuple(self.AUTOMATION_DEFAULTS)).fetchall())
        values = {k: rows.get(k, v) for k, v in self.AUTOMATION_DEFAULTS.items()}
        return {"paused": values["automation_paused"] == "1",
                "max_hops": int(values["max_auto_hops"]),
                "max_per_hour": int(values["max_auto_per_hour"])}

    def set_automation(self, paused: Optional[bool] = None, max_hops: Optional[int] = None,
                       max_per_hour: Optional[int] = None) -> None:
        updates = {"automation_paused": None if paused is None else ("1" if paused else "0"),
                   "max_auto_hops": None if max_hops is None else str(max_hops),
                   "max_auto_per_hour": None if max_per_hour is None else str(max_per_hour)}
        with self._get_connection() as conn:
            for key, value in updates.items():
                if value is not None:
                    conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?)"
                                 " ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))

    def chain_start(self, session_id: Optional[str]) -> Tuple[Optional[int], int]:
        """
        The request whose turn the session is running, and the consecutive
        automatic hops that led to it. A question the user answered for the
        session during that turn resets the count: the user took part.
        """
        if not session_id:
            return None, 0
        with self._get_connection() as conn:
            session = conn.execute("SELECT turn_request_id FROM sessions WHERE id = ?", (session_id,)).fetchone()
            parent_id = session["turn_request_id"] if session else None
            if parent_id is None:
                return None, 0
            parent = conn.execute("SELECT approval, chain_hops, execution_at, delivery_at FROM requests"
                                  " WHERE id = ?", (parent_id,)).fetchone()
            if parent is None or parent["approval"] != "automatic":
                return parent_id, 0
            since = parent["execution_at"] or parent["delivery_at"] or ""
            asked = conn.execute(
                "SELECT 1 FROM requests WHERE sender_session_id = ? AND recipient_id = 'human'"
                " AND decided_at IS NOT NULL AND decided_at >= ? LIMIT 1", (session_id, since)).fetchone()
            return parent_id, 0 if asked else parent["chain_hops"] or 0

    def set_chain(self, request_id: int, parent_id: Optional[int], hops: int) -> None:
        with self._get_connection() as conn:
            conn.execute("UPDATE requests SET parent_request_id = ?, chain_hops = ? WHERE id = ?",
                         (parent_id, hops, request_id))

    def automatic_sends_last_hour(self, relation_id: int) -> int:
        horizon = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        with self._get_connection() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM requests WHERE relation_id = ? AND approval = 'automatic'"
                " AND created_at >= ?", (relation_id, horizon)).fetchone()[0]

    def log_event(self, request_id: int, actor: str, event: str, details: Dict[str, Any]) -> None:
        with self._get_connection() as conn:
            self._log(conn.cursor(), request_id, actor, event, details)

    def hold_automatic(self, actor: str) -> List[int]:
        """
        Stop all: automatic prompts not yet taken for delivery go back to the
        user's review. Prompts already being delivered or running are not stopped.
        """
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cur = conn.cursor()
            ids = [r[0] for r in cur.execute(
                "SELECT id FROM requests WHERE status = 'APPROVED' AND approval = 'automatic'"
                " AND COALESCE(delivery_state, 'waiting') = 'waiting' AND delivering_since IS NULL"
                " AND recipient_id != 'human'").fetchall()]
            for request_id in ids:
                cur.execute("UPDATE requests SET status = 'PENDING', approval = NULL, decided_at = NULL,"
                            " updated_at = ? WHERE id = ? AND status = 'APPROVED'", (now, request_id))
                self._log(cur, request_id, actor, "held", {"reason": "stop_all"})
            return ids

    def init_db(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA journal_mode = WAL;")
            cursor.execute("CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
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
                executing_since TEXT,
                sender_session_id TEXT,
                recipient_session_id TEXT,
                prompt_source TEXT
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
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                provider TEXT,
                entrypoint TEXT,
                cwd TEXT,
                title TEXT,
                tmux_session TEXT,
                state TEXT NOT NULL DEFAULT 'idle',
                turn_request_id INTEGER,
                started_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                waiter_seen_at TEXT,
                ended_at TEXT,
                activity_at TEXT,
                pinned_at TEXT
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
            # Hook events already applied: the bridge may forward one twice.
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS session_events_seen (
                event_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                at TEXT NOT NULL
            );
            """)
            cursor.execute("PRAGMA table_info(requests);")
            cols = [r["name"] for r in cursor.fetchall()]
            for col in ("expires_at", "updated_at", "delivered_at", "priority",
                        "delivering_since", "executing_since",
                        "sender_session_id", "recipient_session_id", "prompt_source"):
                if col not in cols:
                    cursor.execute(f"ALTER TABLE requests ADD COLUMN {col} TEXT;")
            if "delivery_attempts" not in cols:
                cursor.execute(
                    "ALTER TABLE requests ADD COLUMN delivery_attempts INTEGER NOT NULL DEFAULT 0;"
                )
            cursor.execute("PRAGMA table_info(sessions);")
            session_cols = [r["name"] for r in cursor.fetchall()]
            # activity_at: last turn start or end (the waiter's polls do not count);
            # pinned_at: the user added the session to the PWA home.
            # excluded_at: the bridge found a `.placet-off` for its folder (RF-45).
            # suspended_at: the app unloaded the conversation; it can be resumed.
            # model: the model in use as the hooks report it (Antigravity, RF-61).
            for col in ("activity_at", "pinned_at", "excluded_at", "suspended_at", "model"):
                if col not in session_cols:
                    cursor.execute(f"ALTER TABLE sessions ADD COLUMN {col} TEXT;")
            # RFC-02: the stable named node the session is attached to.
            if "node_id" not in session_cols:
                cursor.execute("ALTER TABLE sessions ADD COLUMN node_id INTEGER;")
            # RFC-03/04: reusable relation types and directed relations A -> B.
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS relation_types (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                description TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            """)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS relations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_node INTEGER NOT NULL REFERENCES nodes(id),
                to_node INTEGER NOT NULL REFERENCES nodes(id),
                type_id INTEGER NOT NULL REFERENCES relation_types(id),
                approval TEXT NOT NULL CHECK (approval IN ('manual', 'automatic')),
                created_at TEXT NOT NULL,
                UNIQUE (from_node, to_node, type_id)
            );
            """)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                provider TEXT NOT NULL,
                cwd TEXT,
                created_at TEXT NOT NULL
            );
            """)
            if "suspended_at" not in session_cols:
                # Codex sessions "ended" before were only unloaded by the app.
                cursor.execute(
                    "UPDATE sessions SET suspended_at = ended_at, ended_at = NULL"
                    " WHERE provider = 'codex' AND ended_at IS NOT NULL;"
                )
            # RF-60: usage windows per provider, as last reported by the bridge (JSON).
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS provider_usage"
                " (provider TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            cursor.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY)")
            migration = 'claude_desktop_end_is_suspension_v1'
            if cursor.execute("SELECT 1 FROM schema_migrations WHERE name = ?", (migration,)).fetchone() is None:
                # SessionEnd stops the desktop engine, not the saved conversation.
                # CLI sessions and unknown entrypoints remain genuinely closed.
                cursor.execute(
                    "UPDATE sessions SET suspended_at = COALESCE(suspended_at, ended_at),"
                    " ended_at = NULL, waiter_seen_at = NULL WHERE provider = 'claude-code'"
                    " AND entrypoint = 'claude-desktop' AND ended_at IS NOT NULL"
                )
                cursor.execute("INSERT INTO schema_migrations (name) VALUES (?)", (migration,))
            # The three aspects of a request (RF-29); older rows are backfilled
            # from their status once.
            if "delivery_state" not in cols:
                for col in ("delivery_state", "delivery_at", "delivery_channel", "attempt_id",
                            "execution_state", "execution_at", "reply_state"):
                    if col not in cols:
                        cursor.execute(f"ALTER TABLE requests ADD COLUMN {col} TEXT;")
                cursor.execute("ALTER TABLE requests ADD COLUMN turn_mixed INTEGER NOT NULL DEFAULT 0;")
                cursor.execute(_BACKFILL_ASPECTS_SQL)
            for name, definition in (("allow_auto_return", "INTEGER NOT NULL DEFAULT 0"),
                                     ("return_decision", "TEXT"), ("return_text", "TEXT")):
                if name not in cols:
                    cursor.execute(f"ALTER TABLE requests ADD COLUMN {name} {definition}")
            if "delivery_mode" not in cols:
                cursor.execute("ALTER TABLE requests ADD COLUMN delivery_mode TEXT;")
            if "queue_released_at" not in cols:
                for col in ("queue_released_at", "start_unconfirmed_at", "execution_source"):
                    cursor.execute(f"ALTER TABLE requests ADD COLUMN {col} TEXT;")
                # Older requests never had their turns observed: they must not
                # hold any session's queue.
                cursor.execute(
                    "UPDATE requests SET queue_released_at = COALESCE(updated_at, created_at)"
                    " WHERE status NOT IN ('PENDING', 'APPROVED', 'DELIVERING');"
                )
            if "chain_hops" not in cols:
                # RFC-14: the request whose turn sent this one, and how many
                # consecutive automatic hops led here.
                cursor.execute("ALTER TABLE requests ADD COLUMN parent_request_id INTEGER;")
                cursor.execute("ALTER TABLE requests ADD COLUMN chain_hops INTEGER NOT NULL DEFAULT 0;")
            if "approval" not in cols:
                # Who let the prompt go: "automatic" (rule or relation) or "manual" (the user).
                cursor.execute("ALTER TABLE requests ADD COLUMN approval TEXT;")
            if "return_forward_id" not in cols:
                # The request a reply was forwarded on, to another session.
                cursor.execute("ALTER TABLE requests ADD COLUMN return_forward_id INTEGER;")
            if "relation_id" not in cols:
                # RFC-04: the relation a request travelled on (audit, reply policy).
                cursor.execute("ALTER TABLE requests ADD COLUMN relation_id INTEGER;")
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
                    options, status, created_at, expires_at, updated_at, priority,
                    recipient_session_id, prompt_source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    req.recipient_session_id,
                    req.prompt_source,
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
            sender_session_id=row["sender_session_id"],
            recipient_session_id=row["recipient_session_id"],
            prompt_source=row["prompt_source"],
            response=resp_model,
            delivery=row["delivery_state"] or "waiting",
            delivery_at=_ts(row["delivery_at"]),
            delivery_channel=row["delivery_channel"],
            attempt_id=row["attempt_id"],
            execution=row["execution_state"] or "unobserved",
            execution_at=_ts(row["execution_at"]),
            execution_source=row["execution_source"],
            reply=row["reply_state"] or "none",
            start_unconfirmed_at=_ts(row["start_unconfirmed_at"]),
            queue_released_at=_ts(row["queue_released_at"]),
            delivery_mode=row["delivery_mode"] or "queue",
            allow_auto_return=bool(row["allow_auto_return"]),
            return_decision=row["return_decision"],
            return_text=row["return_text"],
            relation_id=row["relation_id"],
            return_forward_id=row["return_forward_id"],
            approval=row["approval"],
            parent_request_id=row["parent_request_id"],
            chain_hops=row["chain_hops"] or 0,
        )

    def list_requests(
        self,
        status: Optional[RequestStatus] = None,
        limit: int = 50,
        oldest_first: bool = False,
        hide_excluded: bool = False,
    ) -> List[RequestModel]:
        """
        Newest first by default (what a UI wants); the bridge asks for
        oldest_first to process its queue in arrival order.
        """
        order = "ASC" if oldest_first else "DESC"
        conditions = ["r.status = ?"] if status else []
        if hide_excluded:
            conditions.append(_NOT_EXCLUDED_SQL)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
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
        allow_auto_return: bool = False,
        delivery_mode: Optional[str] = None,
        recipient_session_id: Optional[str] = None,
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
                SET allow_auto_return = ?, approval = ?, status = ?, edited_prompt = ?, selected_options = ?, custom_text = ?,
                    decided_at = ?, updated_at = ?,
                    delivery_mode = COALESCE(?, delivery_mode),
                    recipient_session_id = COALESCE(?, recipient_session_id)
                WHERE id = ? AND status IN ({placeholders})
                """,
                [
                    int(allow_auto_return),
                    "automatic" if actor.startswith("system:") else "manual",
                    status.value,
                    edited_prompt,
                    json.dumps(selected_options) if selected_options is not None else None,
                    custom_text,
                    now,
                    now,
                    delivery_mode,
                    recipient_session_id,
                    request_id,
                    *allowed_sources,
                ],
            )
            if cursor.rowcount == 0:
                return None
            details: Dict[str, Any] = {"status": status.value}
            if allow_auto_return:
                details["allow_auto_return"] = True
            if delivery_mode is not None:
                details["delivery_mode"] = delivery_mode
            if recipient_session_id is not None:
                details["recipient_session_id"] = recipient_session_id
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
                UPDATE requests SET status = ?, completed_at = ?, updated_at = ?,
                  -- a reply while delivering proves the prompt arrived
                  delivery_state = CASE WHEN status = 'DELIVERING' THEN 'accepted' ELSE delivery_state END,
                  delivery_at = CASE WHEN status = 'DELIVERING' THEN ? ELSE delivery_at END,
                  reply_state = CASE WHEN ? = 0 THEN 'explicit'
                                     WHEN turn_mixed = 1 THEN 'ambiguous' ELSE 'implicit' END
                WHERE id = ? AND status IN ({",".join("?" for _ in sources)})
                """,
                [RequestStatus.COMPLETED.value, now, now, now, int(implicit), request_id, *sources],
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
                ", delivering_since = ?, delivery_attempts = delivery_attempts + 1,"
                " delivery_state = 'in_progress', delivery_channel = 'tmux', attempt_id = ?",
                [now, _new_attempt()],
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
                ", delivering_since = NULL, delivery_attempts = MAX(delivery_attempts - 1, 0),"
                " delivery_state = 'waiting', attempt_id = NULL",
            ):
                return False
            self._log(cursor, request_id, actor, "injection_released", at=now)
            return True

    def confirm_injection(self, request_id: int, actor: str, via: str) -> Optional[RequestModel]:
        """DELIVERING -> EXECUTING: the agent (or, without acks, the bridge)
        confirmed the prompt arrived; this also settles an uncertain delivery."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if not self._transition(
                cursor, request_id, RequestStatus.DELIVERING, RequestStatus.EXECUTING, now,
                ", executing_since = ?, delivery_state = 'accepted', delivery_at = ?", [now, now],
            ):
                return None
            self._log(cursor, request_id, actor, "acked", {"via": via}, at=now)
        return self.get_request(request_id)

    def mark_delivery_uncertain(self, request_id: int, actor: str, reason: str) -> bool:
        """The send's outcome is unknown: it stays DELIVERING, never re-sent by itself."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE requests SET delivery_state = 'uncertain', updated_at = ?"
                " WHERE id = ? AND status = ? AND delivery_state = 'in_progress'",
                (now, request_id, RequestStatus.DELIVERING.value),
            )
            if cursor.rowcount == 0:
                return False
            self._log(cursor, request_id, actor, "delivery_uncertain", {"reason": reason}, at=now)
            return True

    def retry_delivery(self, request_id: int, actor: str) -> bool:
        """The user asks to send an uncertain delivery again: a new attempt."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE requests SET status = ?, updated_at = ?, delivering_since = NULL,"
                " delivery_state = 'waiting', attempt_id = NULL"
                " WHERE id = ? AND status = ? AND delivery_state = 'uncertain'",
                (RequestStatus.APPROVED.value, now, request_id, RequestStatus.DELIVERING.value),
            )
            if cursor.rowcount == 0:
                return False
            self._log(cursor, request_id, actor, "delivery_retried", at=now)
            return True

    def cancel_delivery(self, request_id: int, actor: str) -> bool:
        """
        The user withdraws a message still waiting or in an uncertain delivery;
        or one already delivered, which the agent may still be working on:
        Placet stops waiting for it, it cannot stop the agent.
        """
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE requests SET status = ?, updated_at = ?, queue_released_at = COALESCE(queue_released_at, ?)"
                " WHERE id = ? AND status IN (?, ?) AND delivery_state = 'accepted'",
                (RequestStatus.CANCELLED.value, now, now, request_id,
                 RequestStatus.EXECUTING.value, RequestStatus.STALLED.value),
            )
            if cursor.rowcount:
                self._log(cursor, request_id, actor, "cancelled", {"after_delivery": True}, at=now)
                return True
            cursor.execute(
                "UPDATE requests SET status = ?, updated_at = ?, delivery_state = 'cancelled'"
                " WHERE id = ? AND ((status = ? AND delivery_state = 'uncertain') OR status = ?)",
                (RequestStatus.CANCELLED.value, now, request_id,
                 RequestStatus.DELIVERING.value, RequestStatus.APPROVED.value),
            )
            if cursor.rowcount == 0:
                return False
            self._log(cursor, request_id, actor, "cancelled", {"delivery": True}, at=now)
            return True

    def uncertain_deliveries(self) -> List[RequestModel]:
        """Uncertain deliveries to a known session, to look for in its conversation."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id FROM requests WHERE status = ? AND delivery_state = 'uncertain'"
                " AND recipient_session_id IS NOT NULL ORDER BY id",
                (RequestStatus.DELIVERING.value,),
            )
            ids = [r["id"] for r in cursor.fetchall()]
        return [r for r in (self.get_request(i) for i in ids) if r is not None]

    def set_execution(self, request_id: int, state: str, actor: str, source: Optional[str] = None) -> bool:
        """Records what the recipient's turn did with a request (RF-29), and who saw it."""
        allowed_from = {
            "started": ("unobserved",),
            "completed": ("unobserved", "started"),
            "interrupted": ("unobserved", "started"),
        }[state]
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"UPDATE requests SET execution_state = ?, execution_at = ?,"
                f" execution_source = COALESCE(?, execution_source)"
                f" WHERE id = ? AND COALESCE(execution_state, 'unobserved')"
                f" IN ({','.join('?' for _ in allowed_from)})",
                (state, now, source, request_id, *allowed_from),
            )
            if cursor.rowcount == 0:
                return False
            self._log(cursor, request_id, actor, f"execution_{state}",
                      {"source": source} if source else None, at=now)
            return True

    def waiting_for_unreachable_sessions(self, grace_seconds: float = 120.0) -> List[int]:
        """
        Messages waiting for a Claude session whose waiter stopped listening
        (RF-26), noted once each. Nobody can restart a waiter from outside:
        the session starts a new one with its next turn.
        """
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        cutoff = (now_dt - timedelta(seconds=grace_seconds)).isoformat()
        waiter_cutoff = (now_dt - timedelta(seconds=WAITER_TTL_SECONDS)).isoformat()
        placeholders = ", ".join("?" for _ in BRIDGE_PROVIDERS)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT r.id FROM requests r JOIN sessions s ON s.id = r.recipient_session_id
                WHERE r.status = ? AND r.created_at <= ?
                  AND s.ended_at IS NULL AND s.excluded_at IS NULL
                  AND COALESCE(s.provider, '') NOT IN ({placeholders})
                  AND (s.waiter_seen_at IS NULL OR s.waiter_seen_at <= ?)
                  AND NOT EXISTS (SELECT 1 FROM request_events e
                                  WHERE e.request_id = r.id AND e.event = 'recipient_unreachable')
                """,
                (RequestStatus.APPROVED.value, cutoff, *BRIDGE_PROVIDERS, waiter_cutoff),
            )
            ids = sorted(r["id"] for r in cursor.fetchall())
            for req_id in ids:
                self._log(cursor, req_id, "system", "recipient_unreachable", at=now)
            return ids

    def unconfirmed_starts(self, seconds: float = START_UNCONFIRMED_SECONDS) -> List[int]:
        """
        Delivered requests whose turn was not seen starting in time: marked
        once, for a warning. They keep holding the session's queue.
        """
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        cutoff = (now_dt - timedelta(seconds=seconds)).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE requests SET start_unconfirmed_at = ?"
                " WHERE start_unconfirmed_at IS NULL AND queue_released_at IS NULL"
                " AND recipient_session_id IS NOT NULL AND delivery_state = 'accepted'"
                " AND COALESCE(execution_state, 'unobserved') = 'unobserved'"
                " AND status IN (?, ?, ?) AND delivery_at <= ? RETURNING id",
                (now, RequestStatus.EXECUTING.value, RequestStatus.STALLED.value,
                 RequestStatus.COMPLETED.value, cutoff),
            )
            ids = sorted(r["id"] for r in cursor.fetchall())
            for req_id in ids:
                self._log(cursor, req_id, "system", "start_unconfirmed", at=now)
            return ids

    def release_queue(self, request_id: int, actor: str, reason: str) -> bool:
        """
        The user lets the session move on past a delivered request: "finished"
        (they say the turn ended) or "queue_next" (send the next one anyway).
        Recorded as their statement: no execution outcome is made up.
        """
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE requests SET queue_released_at = ? WHERE id = ? AND queue_released_at IS NULL"
                " AND delivery_state = 'accepted'",
                (now, request_id),
            )
            if cursor.rowcount == 0:
                return False
            event = "marked_finished_by_user" if reason == "finished" else "queue_overridden"
            self._log(cursor, request_id, actor, event, at=now)
            return True

    # --- "Subito": a message inside the running turn (RF-35) -------------------------

    def set_delivery_mode(self, request_id: int, mode: str) -> None:
        with self._get_connection() as conn:
            conn.execute("UPDATE requests SET delivery_mode = ? WHERE id = ?", (mode, request_id))

    def steer_candidates(self) -> List[Tuple[RequestModel, SessionModel]]:
        """'Now' messages for sessions in the middle of a turn that can take them."""
        placeholders = ", ".join("?" for _ in STEER_PROVIDERS)
        with self._get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT r.id AS request_id, s.id AS session_id FROM requests r
                JOIN sessions s ON s.id = r.recipient_session_id
                WHERE r.status = ? AND r.delivery_mode = 'now' AND s.state = 'busy'
                  AND s.ended_at IS NULL AND s.excluded_at IS NULL AND s.provider IN ({placeholders})
                ORDER BY r.id
                """,
                (RequestStatus.APPROVED.value, *STEER_PROVIDERS),
            ).fetchall()
        out = []
        for row in rows:
            req, session = self.get_request(row["request_id"]), self.get_session(row["session_id"])
            if req is not None and session is not None:
                out.append((req, session))
        return out

    def claim_steer(self, request_id: int, session_id: str, actor: str) -> Optional[RequestModel]:
        """APPROVED -> DELIVERING through the running turn of this session."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if not self._transition(
                cursor, request_id, RequestStatus.APPROVED, RequestStatus.DELIVERING, now,
                ", delivering_since = ?, delivery_attempts = delivery_attempts + 1,"
                " delivery_state = 'in_progress', delivery_channel = 'steer', attempt_id = ?",
                [now, _new_attempt()],
            ):
                return None
            self._log(cursor, request_id, actor, "injection_claimed",
                      {"via": "steer", "session": session_id}, at=now)
        return self.get_request(request_id)

    def steers_waiting(self) -> List[Tuple[RequestModel, SessionModel]]:
        """'Now' messages still waiting for an action of a turn that has ended."""
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT r.id AS request_id, s.id AS session_id FROM requests r
                JOIN sessions s ON s.id = r.recipient_session_id
                WHERE r.status = ? AND r.delivery_channel = 'steer' AND r.delivery_state = 'in_progress'
                  AND (s.state != 'busy' OR s.ended_at IS NOT NULL)
                ORDER BY r.id
                """,
                (RequestStatus.DELIVERING.value,),
            ).fetchall()
        out = []
        for row in rows:
            req, session = self.get_request(row["request_id"]), self.get_session(row["session_id"])
            if req is not None and session is not None:
                out.append((req, session))
        return out

    def steered_in_turn(self, session_id: str) -> List[int]:
        """'Now' messages that entered this session's current turn."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT id FROM requests WHERE recipient_session_id = ? AND status = ?"
                " AND delivery_channel = 'steer' AND delivery_state = 'accepted'"
                " AND COALESCE(execution_state, 'unobserved') IN ('unobserved', 'started')",
                (session_id, RequestStatus.EXECUTING.value),
            ).fetchall()
            return [r["id"] for r in rows]

    def mark_turn_mixed(self, request_id: int) -> None:
        """Another message entered the turn: its implicit reply is ambiguous."""
        with self._get_connection() as conn:
            conn.execute("UPDATE requests SET turn_mixed = 1 WHERE id = ?", (request_id,))

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
                "SELECT id, delivery_attempts FROM requests WHERE status = ? AND delivering_since <= ?"
                " AND COALESCE(delivery_channel, 'tmux') = 'tmux'",
                (RequestStatus.DELIVERING.value, cutoff),
            )
            for row in cursor.fetchall():
                if row["delivery_attempts"] >= max_attempts:
                    if self._transition(cursor, row["id"], RequestStatus.DELIVERING,
                                        RequestStatus.DELIVERY_FAILED, now,
                                        ", delivery_state = 'failed'"):
                        self._log(cursor, row["id"], "system", "injection_failed",
                                  {"attempts": row["delivery_attempts"]}, at=now)
                        failed.append(row["id"])
                elif self._transition(cursor, row["id"], RequestStatus.DELIVERING,
                                      RequestStatus.APPROVED, now,
                                      ", delivering_since = NULL, delivery_state = 'waiting'"):
                    self._log(cursor, row["id"], "system", "injection_requeued",
                              {"attempts": row["delivery_attempts"]}, at=now)
                    requeued.append(row["id"])
        return requeued, failed

    def unconfirmed_to_uncertain(self, ack_timeout_seconds: float) -> List[int]:
        """
        Messages a waiter took or the bridge queued with no confirmation in
        time: their delivery becomes uncertain, never re-sent by itself.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=ack_timeout_seconds)).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id FROM requests WHERE status = ? AND delivering_since <= ?"
                " AND delivery_state = 'in_progress'"
                " AND COALESCE(delivery_channel, 'tmux') NOT IN ('tmux', 'steer')",
                (RequestStatus.DELIVERING.value, cutoff),
            )
            ids = [r["id"] for r in cursor.fetchall()]
        return [i for i in ids if self.mark_delivery_uncertain(i, "system", "no confirmation")]

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
                f"""
                UPDATE requests SET status = ?, updated_at = ?, delivered_at = ?{_CANCEL_DELIVERY_SQL}
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
                UPDATE requests SET status = ?, updated_at = ?{_CANCEL_DELIVERY_SQL}
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
                "UPDATE requests SET delivered_at = ? WHERE id = ? AND delivered_at IS NULL AND (sender_id = 'human' OR recipient_id = 'human' OR status != 'COMPLETED' OR allow_auto_return = 1 OR return_decision IN ('approved', 'forwarded'))",
                (now, request_id),
            )
            if cursor.rowcount == 0:
                return False
            self._log(cursor, request_id, actor, "delivered", {"via": via} if via else None, at=now)
            return True

    def decide_return(self, request_id: int, approve: bool, text: Optional[str], actor: str):
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE requests SET return_decision = ?, return_text = ?, updated_at = ?"
                " WHERE id = ? AND status = 'COMPLETED' AND sender_id != 'human'"
                " AND recipient_id != 'human' AND delivered_at IS NULL"
                " AND allow_auto_return = 0 AND return_decision IS NULL",
                ("approved" if approve else "rejected", text if approve else None,
                 datetime.now(timezone.utc).isoformat(), request_id))
            if not cursor.rowcount:
                return None
            self._log(conn.cursor(), request_id, actor, "return_decided",
                      {"approve": approve, "edited_response": text})
        return self.get_request(request_id)

    def claim_return_forward(self, request_id: int, text: str, actor: str) -> bool:
        """The user forwards a reply elsewhere: the sender gets only a notice."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE requests SET return_decision = 'forwarded', return_text = NULL, updated_at = ?"
                " WHERE id = ? AND status = 'COMPLETED' AND sender_id != 'human'"
                " AND recipient_id != 'human' AND delivered_at IS NULL"
                " AND allow_auto_return = 0 AND return_decision IS NULL",
                (datetime.now(timezone.utc).isoformat(), request_id))
            if not cursor.rowcount:
                return False
            self._log(conn.cursor(), request_id, actor, "return_decided",
                      {"forward": True, "edited_response": text})
            return True

    def set_return_forward(self, request_id: int, forward_id: int, actor: str) -> None:
        with self._get_connection() as conn:
            conn.execute("UPDATE requests SET return_forward_id = ? WHERE id = ?", (forward_id, request_id))
            self._log(conn.cursor(), request_id, actor, "return_forwarded", {"request_id": forward_id})
            self._log(conn.cursor(), forward_id, actor, "forwarded_reply", {"request_id": request_id})

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
                WHERE delivered_at IS NULL AND status != ? AND (sender_id = 'human' OR recipient_id = 'human' OR status != 'COMPLETED' OR allow_auto_return = 1 OR return_decision IN ('approved', 'forwarded'))
                  -- the user's own messages: the outcome is in the conversation
                  AND sender_id != 'human'
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

    # --- Agent sessions (registered by CLI / desktop app hooks) --------------

    @staticmethod
    def _row_to_session(row: sqlite3.Row, waiter_ttl_seconds: float) -> SessionModel:
        waiter = _ts(row["waiter_seen_at"])
        # A session can be woken when its waiter listens (Claude Code), or
        # when the bridge wakes it from outside (Codex, Antigravity).
        reachable = row["ended_at"] is None and row["suspended_at"] is None and (
            row["provider"] in BRIDGE_PROVIDERS
            or (waiter is not None
                and (datetime.now(timezone.utc) - waiter).total_seconds() < waiter_ttl_seconds)
        )
        return SessionModel(
            id=row["id"],
            agent_id=row["agent_id"],
            provider=row["provider"],
            entrypoint=row["entrypoint"],
            cwd=row["cwd"],
            title=row["title"],
            tmux_session=row["tmux_session"],
            state=row["state"],
            turn_request_id=row["turn_request_id"],
            started_at=datetime.fromisoformat(row["started_at"]),
            last_seen_at=datetime.fromisoformat(row["last_seen_at"]),
            waiter_seen_at=waiter,
            ended_at=_ts(row["ended_at"]),
            suspended_at=_ts(row["suspended_at"]),
            model=row["model"],
            activity_at=_ts(row["activity_at"]) or datetime.fromisoformat(row["started_at"]),
            pinned=row["pinned_at"] is not None,
            reachable=reachable and row["excluded_at"] is None,
            excluded=row["excluded_at"] is not None,
            node_id=row["node_id"],
            node_name=row["node_name"] if "node_name" in row.keys() else None,
        )

    def upsert_session(
        self,
        session_id: str,
        agent_id: str,
        provider: Optional[str] = None,
        entrypoint: Optional[str] = None,
        cwd: Optional[str] = None,
        title: Optional[str] = None,
        tmux_session: Optional[str] = None,
        model: Optional[str] = None,
    ) -> bool:
        """
        Registers a session or refreshes it. Returns False if the id is
        already registered to another agent (a token cannot take over
        another agent's session).
        """
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT agent_id FROM sessions WHERE id = ?", (session_id,))
            row = cursor.fetchone()
            if row is not None and row["agent_id"] != agent_id:
                return False
            if row is None:
                cursor.execute(
                    """
                    INSERT INTO sessions (id, agent_id, provider, entrypoint, cwd, title,
                                          tmux_session, model, state, started_at, last_seen_at, activity_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'idle', ?, ?, ?)
                    """,
                    (session_id, agent_id, provider, entrypoint, cwd, title, tmux_session, model, now, now, now),
                )
            else:
                cursor.execute(
                    """
                    UPDATE sessions SET
                        provider = COALESCE(?, provider), entrypoint = COALESCE(?, entrypoint),
                        cwd = COALESCE(?, cwd), title = COALESCE(?, title),
                        tmux_session = COALESCE(?, tmux_session), model = COALESCE(?, model),
                        last_seen_at = ?
                    WHERE id = ?
                    """,
                    (provider, entrypoint, cwd, title, tmux_session, model, now, session_id),
                )
            return True

    def get_session(
        self, session_id: str, waiter_ttl_seconds: float = WAITER_TTL_SECONDS
    ) -> Optional[SessionModel]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"{SESSIONS_WITH_NODE} WHERE sessions.id = ?", (session_id,))
            row = cursor.fetchone()
            return self._row_to_session(row, waiter_ttl_seconds) if row else None

    def list_sessions(
        self,
        agent_id: Optional[str] = None,
        include_ended: bool = False,
        waiter_ttl_seconds: float = WAITER_TTL_SECONDS,
        include_excluded: bool = True,
    ) -> List[SessionModel]:
        where, params = [], []
        if not include_excluded:
            where.append("sessions.excluded_at IS NULL")
        if agent_id is not None:
            where.append("sessions.agent_id = ?")
            params.append(agent_id)
        if not include_ended:
            where.append("sessions.ended_at IS NULL")
        sql = SESSIONS_WITH_NODE
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY sessions.last_seen_at DESC"
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            return [self._row_to_session(r, waiter_ttl_seconds) for r in cursor.fetchall()]

    def set_session_state(
        self, session_id: str, state: str, turn_request_id: Optional[int] = None
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE sessions SET state = ?, turn_request_id = ?, last_seen_at = ?, activity_at = ?"
                " WHERE id = ?",
                (state, turn_request_id, now, now, session_id),
            )

    SEEN_EVENTS_DAYS = 14

    def session_event_seen(self, event_id: str) -> bool:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM session_events_seen WHERE event_id = ?;", (event_id,)
            ).fetchone()
            return row is not None

    def mark_session_event(self, event_id: str, session_id: str) -> None:
        now = datetime.now(timezone.utc)
        horizon = (now - timedelta(days=self.SEEN_EVENTS_DAYS)).isoformat()
        with self._get_connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO session_events_seen (event_id, session_id, at) VALUES (?, ?, ?);",
                (event_id, session_id, now.isoformat()),
            )
            conn.execute("DELETE FROM session_events_seen WHERE at < ?;", (horizon,))

    def untitled_bridge_sessions(self) -> List[SessionModel]:
        """Open Codex and Antigravity sessions: their hooks carry no title, the bridge reads it."""
        placeholders = ", ".join("?" for _ in BRIDGE_PROVIDERS)
        with self._get_connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM sessions WHERE ended_at IS NULL AND excluded_at IS NULL"
                f" AND provider IN ({placeholders}) ORDER BY last_seen_at DESC LIMIT 200",
                BRIDGE_PROVIDERS,
            ).fetchall()
            return [self._row_to_session(r, WAITER_TTL_SECONDS) for r in rows]

    def set_session_titles(self, titles: Dict[str, str]) -> List[str]:
        """Titles the bridge read from the providers' apps; returns the sessions changed."""
        changed = []
        with self._get_connection() as conn:
            for session_id, title in titles.items():
                cursor = conn.execute(
                    "UPDATE sessions SET title = ? WHERE id = ? AND title IS NOT ?",
                    (title[:200], session_id, title[:200]),
                )
                if cursor.rowcount:
                    changed.append(session_id)
        return changed

    def set_session_pinned(self, session_id: str, pinned: bool) -> bool:
        """Adds a session to the PWA home, or removes it; False if unknown."""
        now = datetime.now(timezone.utc).isoformat() if pinned else None
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE sessions SET pinned_at = CASE WHEN ? IS NULL THEN NULL"
                " ELSE COALESCE(pinned_at, ?) END WHERE id = ?",
                (now, now, session_id),
            )
            return cursor.rowcount > 0

    def touch_waiter(self, session_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE sessions SET waiter_seen_at = ?, last_seen_at = ? WHERE id = ?",
                (now, now, session_id),
            )

    # --- Nodes (RFC-02): stable named sessions -------------------------------

    @staticmethod
    def _row_to_node(row: sqlite3.Row) -> NodeModel:
        return NodeModel(id=row["id"], name=row["name"], provider=row["provider"], cwd=row["cwd"],
                         created_at=datetime.fromisoformat(row["created_at"]),
                         session_id=row["session_id"])

    # The node's current session: the attached one seen most recently.
    _NODES_SQL = (
        "SELECT nodes.*, (SELECT s.id FROM sessions s WHERE s.node_id = nodes.id"
        " ORDER BY s.ended_at IS NOT NULL, s.last_seen_at DESC LIMIT 1) AS session_id FROM nodes"
    )

    def list_nodes(self) -> List[NodeModel]:
        with self._get_connection() as conn:
            rows = conn.execute(f"{self._NODES_SQL} ORDER BY nodes.name COLLATE NOCASE").fetchall()
            return [self._row_to_node(r) for r in rows]

    def get_node(self, node_id: int) -> Optional[NodeModel]:
        with self._get_connection() as conn:
            row = conn.execute(f"{self._NODES_SQL} WHERE nodes.id = ?", (node_id,)).fetchone()
            return self._row_to_node(row) if row else None

    def create_node(self, name: str, session_id: str) -> Optional[NodeModel]:
        """
        A node named after the user's choice, with the session attached.
        None if the session does not exist; NodeNameTaken if the name is used.
        """
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            session = conn.execute("SELECT provider, cwd FROM sessions WHERE id = ?",
                                   (session_id,)).fetchone()
            if session is None:
                return None
            try:
                cur = conn.execute(
                    "INSERT INTO nodes (name, provider, cwd, created_at) VALUES (?, ?, ?, ?)",
                    (name, session["provider"] or "unknown", session["cwd"], now))
            except sqlite3.IntegrityError:
                raise NodeNameTaken(name) from None
            conn.execute("UPDATE sessions SET node_id = ? WHERE id = ?", (cur.lastrowid, session_id))
            node_id = cur.lastrowid
        return self.get_node(node_id)

    def rename_node(self, node_id: int, name: str) -> bool:
        with self._get_connection() as conn:
            try:
                cur = conn.execute("UPDATE nodes SET name = ? WHERE id = ?", (name, node_id))
            except sqlite3.IntegrityError:
                raise NodeNameTaken(name) from None
            return cur.rowcount > 0

    def delete_node(self, node_id: int) -> List[str]:
        """Removes the node and its relations; its sessions stay, detached. Returns them."""
        with self._get_connection() as conn:
            ids = [r["id"] for r in conn.execute("SELECT id FROM sessions WHERE node_id = ?", (node_id,))]
            conn.execute("DELETE FROM relations WHERE from_node = ? OR to_node = ?", (node_id, node_id))
            conn.execute("UPDATE sessions SET node_id = NULL WHERE node_id = ?", (node_id,))
            cur = conn.execute("DELETE FROM nodes WHERE id = ?", (node_id,))
            return ids if cur.rowcount else []

    def attach_session(self, session_id: str, node_id: Optional[int]) -> str:
        """
        Attaches a session to a node (None detaches it). Returns "ok",
        "session" or "node" (not found) or "provider" (the node belongs to
        another provider: a node is one conversation of one app).
        """
        with self._get_connection() as conn:
            session = conn.execute("SELECT provider, cwd FROM sessions WHERE id = ?",
                                   (session_id,)).fetchone()
            if session is None:
                return "session"
            if node_id is not None:
                node = conn.execute("SELECT provider FROM nodes WHERE id = ?", (node_id,)).fetchone()
                if node is None:
                    return "node"
                if node["provider"] != (session["provider"] or "unknown"):
                    return "provider"
                conn.execute("UPDATE nodes SET cwd = COALESCE(?, cwd) WHERE id = ?",
                             (session["cwd"], node_id))
            conn.execute("UPDATE sessions SET node_id = ? WHERE id = ?", (node_id, session_id))
            return "ok"

    def reattach_after_clear(self, session_id: str, seconds: float = REATTACH_SECONDS) -> Optional[int]:
        """
        `/clear` ends a conversation and starts a new one with another id:
        the new session takes the node of the one that just ended in the
        same folder, for the same agent and provider (and tmux session, if
        any). Returns the node id, or None.
        """
        horizon = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
        with self._get_connection() as conn:
            new = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if new is None or new["node_id"] is not None or not new["cwd"]:
                return None
            old = conn.execute(
                "SELECT node_id FROM sessions WHERE id != ? AND node_id IS NOT NULL"
                " AND agent_id = ? AND provider IS ? AND cwd = ? AND tmux_session IS ?"
                " AND COALESCE(ended_at, suspended_at) >= ?"
                # A node with an open conversation keeps it.
                " AND NOT EXISTS (SELECT 1 FROM sessions o WHERE o.node_id = sessions.node_id"
                " AND o.ended_at IS NULL AND o.suspended_at IS NULL)"
                " ORDER BY COALESCE(ended_at, suspended_at) DESC LIMIT 1",
                (session_id, new["agent_id"], new["provider"], new["cwd"], new["tmux_session"], horizon),
            ).fetchone()
            if old is None:
                return None
            conn.execute("UPDATE sessions SET node_id = ? WHERE id = ?", (old["node_id"], session_id))
            return old["node_id"]

    # --- Relation types and relations (RFC-03, RFC-04) -----------------------

    @staticmethod
    def _row_to_type(row: sqlite3.Row) -> RelationTypeModel:
        return RelationTypeModel(id=row["id"], name=row["name"], description=row["description"],
                                 created_at=datetime.fromisoformat(row["created_at"]))

    def list_relation_types(self) -> List[RelationTypeModel]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM relation_types ORDER BY name COLLATE NOCASE").fetchall()
            return [self._row_to_type(r) for r in rows]

    def get_relation_type(self, type_id: int) -> Optional[RelationTypeModel]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM relation_types WHERE id = ?", (type_id,)).fetchone()
            return self._row_to_type(row) if row else None

    def create_relation_type(self, name: str, description: str) -> RelationTypeModel:
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            try:
                cur = conn.execute(
                    "INSERT INTO relation_types (name, description, created_at) VALUES (?, ?, ?)",
                    (name, description, now))
            except sqlite3.IntegrityError:
                raise NameTaken(name) from None
            type_id = cur.lastrowid
        return self.get_relation_type(type_id)

    def update_relation_type(self, type_id: int, name: Optional[str], description: Optional[str]) -> bool:
        with self._get_connection() as conn:
            try:
                cur = conn.execute(
                    "UPDATE relation_types SET name = COALESCE(?, name),"
                    " description = COALESCE(?, description) WHERE id = ?",
                    (name, description, type_id))
            except sqlite3.IntegrityError:
                raise NameTaken(name) from None
            return cur.rowcount > 0

    def delete_relation_type(self, type_id: int) -> str:
        """"ok", "missing", or "in_use" (relations of this type must go first)."""
        with self._get_connection() as conn:
            if conn.execute("SELECT 1 FROM relations WHERE type_id = ?", (type_id,)).fetchone():
                return "in_use"
            cur = conn.execute("DELETE FROM relation_types WHERE id = ?", (type_id,))
            return "ok" if cur.rowcount else "missing"

    _RELATIONS_SQL = (
        "SELECT r.*, f.name AS from_name, t.name AS to_name, y.name AS type_name,"
        " y.description AS type_description FROM relations r"
        " JOIN nodes f ON f.id = r.from_node JOIN nodes t ON t.id = r.to_node"
        " JOIN relation_types y ON y.id = r.type_id"
    )

    @staticmethod
    def _row_to_relation(row: sqlite3.Row) -> RelationModel:
        return RelationModel(
            id=row["id"], from_node=row["from_node"], to_node=row["to_node"], type_id=row["type_id"],
            approval=row["approval"], created_at=datetime.fromisoformat(row["created_at"]),
            from_name=row["from_name"], to_name=row["to_name"], type_name=row["type_name"],
            type_description=row["type_description"],
        )

    def list_relations(self, node_id: Optional[int] = None) -> List[RelationModel]:
        sql, params = self._RELATIONS_SQL, []
        if node_id is not None:
            sql += " WHERE r.from_node = ? OR r.to_node = ?"
            params = [node_id, node_id]
        with self._get_connection() as conn:
            rows = conn.execute(sql + " ORDER BY f.name, t.name, y.name", params).fetchall()
            return [self._row_to_relation(r) for r in rows]

    def get_relation(self, relation_id: int) -> Optional[RelationModel]:
        with self._get_connection() as conn:
            row = conn.execute(f"{self._RELATIONS_SQL} WHERE r.id = ?", (relation_id,)).fetchone()
            return self._row_to_relation(row) if row else None

    def create_relation(self, from_node: int, to_node: int, type_id: int, approval: str) -> str | RelationModel:
        """The new relation, or "nodes" / "type" (not found), "self", "exists"."""
        if from_node == to_node:
            return "self"
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            found = conn.execute("SELECT COUNT(*) AS n FROM nodes WHERE id IN (?, ?)",
                                 (from_node, to_node)).fetchone()["n"]
            if found != 2:
                return "nodes"
            if conn.execute("SELECT 1 FROM relation_types WHERE id = ?", (type_id,)).fetchone() is None:
                return "type"
            try:
                cur = conn.execute(
                    "INSERT INTO relations (from_node, to_node, type_id, approval, created_at)"
                    " VALUES (?, ?, ?, ?, ?)", (from_node, to_node, type_id, approval, now))
            except sqlite3.IntegrityError:
                return "exists"
            relation_id = cur.lastrowid
        return self.get_relation(relation_id)

    def set_relation_approval(self, relation_id: int, approval: str) -> bool:
        with self._get_connection() as conn:
            cur = conn.execute("UPDATE relations SET approval = ? WHERE id = ?", (approval, relation_id))
            return cur.rowcount > 0

    def delete_relation(self, relation_id: int) -> bool:
        with self._get_connection() as conn:
            return conn.execute("DELETE FROM relations WHERE id = ?", (relation_id,)).rowcount > 0

    def find_relation(self, from_node: Optional[int], to_node: Optional[int],
                      type_id: Optional[int] = None) -> Optional[RelationModel]:
        """
        The relation A -> B a request travels on. Without a type, only when
        A -> B has exactly one relation: Placet never guesses between types.
        """
        if from_node is None or to_node is None:
            return None
        sql, params = f"{self._RELATIONS_SQL} WHERE r.from_node = ? AND r.to_node = ?", [from_node, to_node]
        if type_id is not None:
            sql += " AND r.type_id = ?"
            params.append(type_id)
        with self._get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return self._row_to_relation(rows[0]) if len(rows) == 1 else None

    def redirect_request(self, request_id: int, recipient_id: Optional[str],
                         relation_id: Optional[int], recipient_session_id: str, actor: str) -> bool:
        """
        The user sends a pending prompt to another session, possibly of
        another agent (recipient_id None keeps the agent). False if no
        longer pending.
        """
        with self._get_connection() as conn:
            cur = conn.cursor()
            row = cur.execute("SELECT recipient_id, recipient_session_id FROM requests"
                              " WHERE id = ? AND status = 'PENDING'", (request_id,)).fetchone()
            if row is None:
                return False
            cur.execute(
                "UPDATE requests SET recipient_id = COALESCE(?, recipient_id), relation_id = ?,"
                " recipient_session_id = ? WHERE id = ? AND status = 'PENDING'",
                (recipient_id, relation_id, recipient_session_id, request_id))
            if cur.rowcount == 0:
                return False
            self._log(cur, request_id, actor, "redirected", {
                "from": row["recipient_id"], "from_session": row["recipient_session_id"],
                "to": recipient_id or row["recipient_id"], "to_session": recipient_session_id,
                "relation_id": relation_id})
            return True

    def node_by_name(self, name: str) -> Optional[NodeModel]:
        with self._get_connection() as conn:
            row = conn.execute(f"{self._NODES_SQL} WHERE nodes.name = ? COLLATE NOCASE", (name,)).fetchone()
            return self._row_to_node(row) if row else None

    def relation_type_by_name(self, name: str) -> Optional[RelationTypeModel]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM relation_types WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
            return self._row_to_type(row) if row else None

    def outgoing_relations(self, node_id: int) -> List[RelationModel]:
        with self._get_connection() as conn:
            rows = conn.execute(f"{self._RELATIONS_SQL} WHERE r.from_node = ? ORDER BY t.name, y.name",
                                (node_id,)).fetchall()
            return [self._row_to_relation(r) for r in rows]

    def set_request_relation(self, request_id: int, relation_id: int) -> None:
        with self._get_connection() as conn:
            conn.execute("UPDATE requests SET relation_id = ? WHERE id = ?", (relation_id, request_id))

    def set_usage(self, provider: str, data: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute(
                "INSERT INTO provider_usage (provider, data, updated_at) VALUES (?, ?, ?)"
                " ON CONFLICT(provider) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
                (provider, data, now),
            )

    def list_usage(self) -> List[str]:
        with self._get_connection() as conn:
            return [r["data"] for r in conn.execute("SELECT data FROM provider_usage ORDER BY provider")]

    def limited_providers(self, now: Optional[datetime] = None) -> Set[str]:
        """
        Providers whose account has used up a usage window (RF-60): a message
        sent now would start a turn that fails at once, and the app does not
        retry it. Their mail waits in Placet until the window resets. Only
        account-wide windows count: Antigravity's limits are per model group,
        and Placet does not know which model a conversation will use.
        """
        from backend.app.models import ProviderUsage
        now = now or datetime.now(timezone.utc)
        limited = set()
        for data in self.list_usage():
            usage = ProviderUsage.model_validate_json(data)
            if any(b.group is None and b.used_percent >= 100 and b.resets_at and b.resets_at > now
                   for b in usage.buckets):
                limited.add(usage.provider)
        return limited

    def reopen_session(self, session_id: str) -> None:
        """The session runs again (it started, or a turn began): neither ended nor suspended."""
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE sessions SET ended_at = NULL, suspended_at = NULL WHERE id = ?", (session_id,)
            )

    def suspend_session(self, session_id: str) -> None:
        """
        The app unloaded the conversation (Codex: idle, restart, update). It
        can be resumed: it stays listed, and its messages wait in Placet.
        """
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE sessions SET suspended_at = ?, state = 'idle', waiter_seen_at = NULL WHERE id = ?",
                (now, session_id),
            )

    def end_session(self, session_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE sessions SET ended_at = ?, state = 'idle', waiter_seen_at = NULL WHERE id = ?",
                (now, session_id),
            )

    def link_sender_session(self, request_id: int, session_id: str, agent_id: str) -> bool:
        """Remembers which session made a request, so the outcome goes back there."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE requests SET sender_session_id = ?
                WHERE id = ? AND sender_id = ? AND sender_session_id IS NULL
                """,
                (session_id, request_id, agent_id),
            )
            return cursor.rowcount > 0

    def set_recipient_session(self, request_id: int, session_id: Optional[str]) -> None:
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE requests SET recipient_session_id = ? WHERE id = ?",
                (session_id, request_id),
            )

    # --- Session inbox (hook delivery channel) -------------------------------

    _OUTCOMES_SQL = """
        SELECT id FROM requests
        WHERE sender_session_id = ? AND delivered_at IS NULL AND status != ? AND (sender_id = 'human' OR recipient_id = 'human' OR status != 'COMPLETED' OR allow_auto_return = 1 OR return_decision IN ('approved', 'forwarded'))
        ORDER BY updated_at ASC
    """
    _INJECTIONS_SQL = """
        SELECT id FROM requests
        WHERE status = ? AND recipient_id = ?
          -- the last parameter: 1 = only requests addressed to this very session
          AND (recipient_session_id = ? OR (recipient_session_id IS NULL AND ? = 0))
          -- a session never receives its own request (agent -> same agent)
          AND (sender_session_id IS NULL OR sender_session_id != ?)
        ORDER BY (recipient_session_id IS NULL) ASC, id ASC
    """

    def known_folders(self, limit: int = 500) -> List[str]:
        """The folders of known sessions, for the bridge to look for `.placet-off`."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT cwd, MAX(last_seen_at) AS seen FROM sessions WHERE cwd IS NOT NULL"
                " GROUP BY cwd ORDER BY seen DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [r["cwd"] for r in rows]

    def set_excluded_folders(
        self, checked: Sequence[str], excluded: Sequence[str], actor: str = "bridge"
    ) -> Tuple[List[str], List[int]]:
        """
        Applies what the bridge found: sessions in an excluded folder are
        hidden and get nothing delivered, their messages not yet delivered
        are cancelled with the historical project-exclusion reason; a folder no longer excluded comes
        back. Returns (sessions changed, requests cancelled).
        """
        now = datetime.now(timezone.utc).isoformat()
        excluded_set = set(excluded)
        changed, cancelled = [], []
        with self._get_connection() as conn:
            cursor = conn.cursor()
            for cwd in set(checked):
                if cwd in excluded_set:
                    cursor.execute(
                        "UPDATE sessions SET excluded_at = ? WHERE cwd = ? AND excluded_at IS NULL RETURNING id",
                        (now, cwd),
                    )
                else:
                    cursor.execute(
                        "UPDATE sessions SET excluded_at = NULL WHERE cwd = ? AND excluded_at IS NOT NULL"
                        " RETURNING id",
                        (cwd,),
                    )
                ids = [r["id"] for r in cursor.fetchall()]
                changed.extend(ids)
                if cwd not in excluded_set or not ids:
                    continue
                marks = ",".join("?" for _ in ids)
                cursor.execute(
                    f"UPDATE requests SET status = ?, updated_at = ?, delivery_state = 'cancelled'"
                    f" WHERE recipient_session_id IN ({marks}) AND (status = ?"
                    f" OR (status = ? AND delivery_state IN ('in_progress', 'uncertain')))"
                    f" RETURNING id",
                    (RequestStatus.CANCELLED.value, now, *ids,
                     RequestStatus.APPROVED.value, RequestStatus.DELIVERING.value),
                )
                for r in cursor.fetchall():
                    self._log(cursor, r["id"], actor, "cancelled", {"reason": "progetto escluso"}, at=now)
                    cancelled.append(r["id"])
        return changed, sorted(cancelled)

    def request_in_flight(self, session_id: str) -> Optional[int]:
        """
        The request this session is still dealing with, if any (RF-29b): one
        being delivered (an uncertain one too), or one delivered whose turn
        has not ended. The next waits until it concludes.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT id FROM requests WHERE recipient_session_id = ? AND queue_released_at IS NULL AND (
                  status = ?
                  OR (status IN (?, ?, ?)
                      AND COALESCE(execution_state, 'unobserved') IN ('unobserved', 'started'))
                ) ORDER BY id LIMIT 1
                """,
                (session_id, RequestStatus.DELIVERING.value, RequestStatus.EXECUTING.value,
                 RequestStatus.STALLED.value, RequestStatus.COMPLETED.value),
            ).fetchone()
            return row["id"] if row else None

    def inbox_has_message(self, session: SessionModel, explicit_only: bool = False) -> bool:
        """Whether take_inbox_message would hand something out now (read only)."""
        if session.state != "idle" or session.ended_at is not None or session.excluded:
            return False
        if session.provider in self.limited_providers():
            return False
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(self._OUTCOMES_SQL, (session.id, RequestStatus.PENDING.value))
            outcome_ids = [r["id"] for r in cursor.fetchall()]
            cursor.execute(
                self._INJECTIONS_SQL,
                (RequestStatus.APPROVED.value, session.agent_id, session.id, int(explicit_only), session.id),
            )
            if cursor.fetchone() is not None and self.request_in_flight(session.id) is None:
                return True
        return any(
            (req := self.get_request(i)) is not None and is_settled(req) for i in outcome_ids
        )

    def claude_sessions_to_wake(self) -> List[SessionModel]:
        """Only explicitly addressed idle desktop sessions with no fresh waiter."""
        return [s for s in self.list_sessions()
                if s.provider == 'claude-code' and s.entrypoint == 'claude-desktop'
                and not s.reachable and self.inbox_has_message(s, explicit_only=True)]

    def bridge_sessions_with_mail(self, include_suspended: bool = False) -> List[SessionModel]:
        """Open sessions the bridge wakes (Codex, Antigravity) with a message waiting."""
        placeholders = ", ".join("?" for _ in BRIDGE_PROVIDERS)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT * FROM sessions WHERE ended_at IS NULL"
                + (" AND (suspended_at IS NULL OR provider = 'codex')" if include_suspended
                   else " AND suspended_at IS NULL")
                + f" AND provider IN ({placeholders})",
                BRIDGE_PROVIDERS,
            )
            sessions = [self._row_to_session(r, WAITER_TTL_SECONDS) for r in cursor.fetchall()]
        # Not for a provider at its usage limit: the message would be lost.
        limited = self.limited_providers()
        return [s for s in sessions if s.provider not in limited and self.inbox_has_message(s)]

    def take_inbox_message(
        self, session: SessionModel, actor: Optional[str] = None, via: str = "hook"
    ) -> Optional[Tuple[str, RequestModel]]:
        """
        Atomically takes the next message for an idle session: first an
        outcome of a request it made, then a prompt addressed to it (or to its
        agent, if not addressed to a specific session). Returns
        ("outcome" | "injection", request) or None. `actor` and `via` say who
        takes it for the session (its waiter, or the bridge).
        """
        if session.state != "idle" or session.ended_at is not None or session.excluded:
            return None
        # A provider at its usage limit would lose the message (RF-60).
        if session.provider in self.limited_providers():
            return None
        actor = actor or f"agent:{session.agent_id}"
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(self._OUTCOMES_SQL, (session.id, RequestStatus.PENDING.value))
            outcome_ids = [r["id"] for r in cursor.fetchall()]
        for req_id in outcome_ids:
            req = self.get_request(req_id)
            if req is not None and is_settled(req) and self.mark_delivered(req_id, actor=actor, via=via):
                return "outcome", self.get_request(req_id)

        if self.request_in_flight(session.id) is not None:
            return None
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                self._INJECTIONS_SQL,
                (RequestStatus.APPROVED.value, session.agent_id, session.id, 0, session.id),
            )
            candidates = [r["id"] for r in cursor.fetchall()]
            for req_id in candidates:
                # Claim and bind to this session in one statement: with several
                # sessions of the same agent, the first idle one wins.
                if not self._transition(
                    cursor, req_id, RequestStatus.APPROVED, RequestStatus.DELIVERING, now,
                    ", delivering_since = ?, delivery_attempts = delivery_attempts + 1,"
                    " recipient_session_id = ?, delivery_state = 'in_progress', delivery_channel = ?,"
                    " attempt_id = ?",
                    [now, session.id, "bridge" if via == "bridge" else "waiter", _new_attempt()],
                ):
                    continue
                cursor.execute("SELECT delivery_attempts FROM requests WHERE id = ?", (req_id,))
                attempt = cursor.fetchone()["delivery_attempts"]
                self._log(cursor, req_id, actor, "injection_claimed",
                          {"attempt": attempt, "via": via, "session": session.id}, at=now)
                break
            else:
                return None
        return "injection", self.get_request(req_id)

    # --- Channel selection ----------------------------------------------------

    def tmux_work_filter(
        self,
        injections: List[RequestModel],
        results: List[RequestModel],
        tmux_session_of: Callable[[str], Optional[str]],
    ) -> Tuple[List[RequestModel], List[RequestModel], List[str]]:
        """
        Keeps only what the tmux bridge should deliver; the rest goes through
        session inboxes (hook channel). A message bound to a session goes to
        that session only: through its inbox while its waiter is listening,
        through tmux only if it is the agent's configured tmux session.
        Also returns agents whose tmux session is busy per its hooks.
        """
        sessions = {s.id: s for s in self.list_sessions(include_ended=True)}

        def via_tmux(session_id: Optional[str], agent_id: str) -> bool:
            if session_id is None:
                return True
            session = sessions.get(session_id)
            if session is None or session.reachable:
                return False
            return session.tmux_session is not None and session.tmux_session == tmux_session_of(agent_id)

        reachable_agents = {s.agent_id for s in sessions.values() if s.reachable}
        tmux_injections = [
            r for r in injections
            if (r.recipient_session_id is None and r.recipient_id not in reachable_agents)
            or (r.recipient_session_id is not None and via_tmux(r.recipient_session_id, r.recipient_id))
        ]
        tmux_results = [r for r in results if via_tmux(r.sender_session_id, r.sender_id)]
        busy = [
            s.agent_id for s in sessions.values()
            if s.ended_at is None and s.state == "busy"
            and s.tmux_session is not None and s.tmux_session == tmux_session_of(s.agent_id)
        ]
        return tmux_injections, tmux_results, busy
