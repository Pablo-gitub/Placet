import sqlite3
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Set, Tuple, Union

import uuid
from pathlib import Path
from backend.app.models import (
    BRIDGE_PROVIDERS,
    STEER_PROVIDERS,
    HOLD_ALERT_SECONDS,
    PAUSE_UNRESUMED_SECONDS,
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
    ProjectModel,
    ProjectSummaryModel,
    ProjectDetailModel,
    ProjectRelationDetail,
    WAITER_TTL_SECONDS,
    DEFAULT_EXPIRES_IN_SECONDS,
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



# Providers whose usage the bridge polls: an old reading means it failed (RF-60).
USAGE_POLLED = {"claude-code", "antigravity"}
USAGE_STALE_AFTER = timedelta(minutes=20)

class NameTaken(ValueError):
    """Another node or relation type already has this name (names ignore case)."""


NodeNameTaken = NameTaken
ProjectNameTaken = NameTaken


def _new_attempt() -> str:
    return uuid.uuid4().hex[:8]


def _ts(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


def _normalize_utc_iso(val: Union[str, datetime, None]) -> Optional[str]:
    if not val:
        return None
    if isinstance(val, str):
        try:
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
        except Exception:
            return val
    elif isinstance(val, datetime):
        dt = val
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class Database:
    def __init__(self, db_path: str = "placet.db"):
        self.db_path = db_path
        self._last_usage_purge_time = 0.0

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

    def get_setting(self, key: str) -> Optional[str]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self._get_connection() as conn:
            conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?)"
                         " ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))

    def usage_guard_config(self) -> Dict[str, Any]:
        """Current usage guard settings from the database (or defaults)."""
        def percent(key: str, default: int) -> int:
            try:
                return int(self.get_setting(key) or default)
            except ValueError:
                return default
        return {
            "enabled": self.get_setting("usage_guard_enabled") == "true",
            "five_hour": percent("usage_guard_5h", 90),
            "seven_day": percent("usage_guard_7d", 95),
            "deliver_at_reset": self.get_setting("deliver_at_reset") == "true",
        }

    def add_push_subscription(self, endpoint: str, p256dh: str, auth: str, user_agent: Optional[str]) -> None:
        with self._get_connection() as conn:
            conn.execute(
                "INSERT INTO push_subscriptions (endpoint, p256dh, auth, user_agent, created_at)"
                " VALUES (?, ?, ?, ?, ?) ON CONFLICT(endpoint) DO UPDATE SET p256dh = excluded.p256dh,"
                " auth = excluded.auth, user_agent = excluded.user_agent",
                (endpoint, p256dh, auth, (user_agent or "")[:200], datetime.now(timezone.utc).isoformat()))

    def delete_push_subscription(self, endpoint: str) -> bool:
        with self._get_connection() as conn:
            return conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,)).rowcount > 0

    def list_push_subscriptions(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT endpoint, p256dh, auth FROM push_subscriptions").fetchall()
        return [{"endpoint": r["endpoint"], "keys": {"p256dh": r["p256dh"], "auth": r["auth"]}} for r in rows]

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

    DEFAULT_NOTIFICATION_PREFERENCES = {
        "needs_you": True,
        "problems": True,
        "turn_end": "placet",
        "queue_held": True,
    }

    def get_notification_preferences(self) -> Dict[str, Any]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT value FROM app_settings WHERE key = 'notification_preferences'").fetchone()
        if not row:
            return dict(self.DEFAULT_NOTIFICATION_PREFERENCES)
        try:
            stored = json.loads(row[0])
            if not isinstance(stored, dict):
                return dict(self.DEFAULT_NOTIFICATION_PREFERENCES)
            return {
                "needs_you": bool(stored.get("needs_you", True)),
                "problems": bool(stored.get("problems", True)),
                "turn_end": stored.get("turn_end") if stored.get("turn_end") in ("all", "placet", "off") else "placet",
                "queue_held": bool(stored.get("queue_held", True)),
            }
        except Exception:
            return dict(self.DEFAULT_NOTIFICATION_PREFERENCES)

    def set_notification_preferences(self, prefs: Dict[str, Any]) -> None:
        val = {
            "needs_you": bool(prefs.get("needs_you", True)),
            "problems": bool(prefs.get("problems", True)),
            "turn_end": prefs.get("turn_end") if prefs.get("turn_end") in ("all", "placet", "off") else "placet",
            "queue_held": bool(prefs.get("queue_held", True)),
        }
        with self._get_connection() as conn:
            conn.execute(
                "INSERT INTO app_settings (key, value) VALUES ('notification_preferences', ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (json.dumps(val),),
            )

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
                prompt_source TEXT,
                project_id INTEGER,
                project_context TEXT,
                relation_type_name TEXT,
                scheduled_at TEXT,
                expires_in_seconds INTEGER NOT NULL DEFAULT 86400
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
                pinned_at TEXT,
                continued_to TEXT,
                prompt_count INTEGER NOT NULL DEFAULT 0
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
            # continued_to: RF-29d session continuation.
            for col in ("activity_at", "pinned_at", "excluded_at", "suspended_at", "model", "continued_to"):
                if col not in session_cols:
                    cursor.execute(f"ALTER TABLE sessions ADD COLUMN {col} TEXT;")
            if "prompt_count" not in session_cols:
                cursor.execute("ALTER TABLE sessions ADD COLUMN prompt_count INTEGER NOT NULL DEFAULT 0;")
                cursor.execute(
                    "UPDATE sessions SET prompt_count = 1"
                    " WHERE activity_at > started_at OR turn_request_id IS NOT NULL OR ended_at IS NOT NULL;"
                )
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
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                description TEXT NOT NULL DEFAULT '',
                relation_rule TEXT,
                max_auto_hops INTEGER,
                max_auto_per_hour INTEGER,
                instructions TEXT,
                created_at TEXT NOT NULL
            );
            """)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS project_nodes (
                project_id INTEGER NOT NULL REFERENCES projects(id),
                node_id INTEGER NOT NULL REFERENCES nodes(id),
                instructions TEXT,
                PRIMARY KEY (project_id, node_id)
            );
            """)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS project_relations (
                project_id INTEGER NOT NULL REFERENCES projects(id),
                relation_id INTEGER NOT NULL REFERENCES relations(id),
                PRIMARY KEY (project_id, relation_id)
            );
            """)
            if "suspended_at" not in session_cols:
                # Codex sessions "ended" before were only unloaded by the app.
                cursor.execute(
                    "UPDATE sessions SET suspended_at = ended_at, ended_at = NULL"
                    " WHERE provider = 'codex' AND ended_at IS NOT NULL;"
                )
            # Web Push subscriptions of the PWA, one per device/browser.
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS push_subscriptions (endpoint TEXT PRIMARY KEY,"
                " p256dh TEXT NOT NULL, auth TEXT NOT NULL, user_agent TEXT, created_at TEXT NOT NULL)"
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
            migration_samples = 'usage_samples_v1'
            if cursor.execute("SELECT 1 FROM schema_migrations WHERE name = ?", (migration_samples,)).fetchone() is None:
                cursor.execute(
                    "CREATE TABLE IF NOT EXISTS usage_samples ("
                    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "  provider TEXT NOT NULL,"
                    "  observed_at TEXT NOT NULL,"
                    "  window TEXT NOT NULL,"
                    "  bucket_id TEXT NOT NULL,"
                    "  bucket_group TEXT,"
                    "  used_percent REAL NOT NULL,"
                    "  resets_at TEXT"
                    ");"
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_usage_samples_provider_observed"
                    " ON usage_samples (provider, observed_at);"
                )
                cursor.execute("INSERT INTO schema_migrations (name) VALUES (?)", (migration_samples,))
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
            if "hold_alerted_at" not in cols:
                cursor.execute("ALTER TABLE requests ADD COLUMN hold_alerted_at TEXT;")
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
            if "project_id" not in cols:
                # RFC-15: project context reference on user messages
                cursor.execute("ALTER TABLE requests ADD COLUMN project_id INTEGER;")
            if "project_context" not in cols:
                # RFC-15: composed project and node instructions block
                cursor.execute("ALTER TABLE requests ADD COLUMN project_context TEXT;")
            # RFC-16: the latest progress update of a running request.
            for col in ("progress_text", "progress_at"):
                if col not in cols:
                    cursor.execute(f"ALTER TABLE requests ADD COLUMN {col} TEXT;")
            if "relation_type_name" not in cols:
                # RF-64: historical relation type name on requests
                cursor.execute("ALTER TABLE requests ADD COLUMN relation_type_name TEXT;")
            if "scheduled_at" not in cols:
                # RF-65: scheduled delivery timestamp in UTC ISO format
                cursor.execute("ALTER TABLE requests ADD COLUMN scheduled_at TEXT;")
            if "expires_in_seconds" not in cols:
                cursor.execute("ALTER TABLE requests ADD COLUMN expires_in_seconds INTEGER NOT NULL DEFAULT 86400;")
            # RFC-16: each relation has its own reply policy, initially equal to
            # its outbound policy; replies no longer depend on the reverse relation.
            rel_cols = [row["name"] for row in cursor.execute("PRAGMA table_info(relations);").fetchall()]
            if rel_cols and "reply_approval" not in rel_cols:
                cursor.execute("ALTER TABLE relations ADD COLUMN reply_approval TEXT NOT NULL DEFAULT 'manual'"
                               " CHECK (reply_approval IN ('manual', 'automatic'));")
            if cursor.execute("SELECT 1 FROM schema_migrations WHERE name = 'relation_reply_approval_v1'").fetchone() is None:
                if "approval" in rel_cols:
                    cursor.execute("UPDATE relations SET reply_approval = approval;")
                cursor.execute("INSERT INTO schema_migrations (name) VALUES ('relation_reply_approval_v1')")
            migration_rtn = 'request_relation_type_name_v1'
            if cursor.execute("SELECT 1 FROM schema_migrations WHERE name = ?", (migration_rtn,)).fetchone() is None:
                cursor.execute("PRAGMA table_info(requests);")
                current_req_cols = [r["name"] for r in cursor.fetchall()]
                if "relation_type_name" not in current_req_cols:
                    cursor.execute("ALTER TABLE requests ADD COLUMN relation_type_name TEXT;")
                cursor.execute(
                    "UPDATE requests SET relation_type_name = ("
                    "  SELECT rt.name FROM relations rel"
                    "  JOIN relation_types rt ON rt.id = rel.type_id"
                    "  WHERE rel.id = requests.relation_id"
                    ") WHERE relation_id IS NOT NULL AND relation_type_name IS NULL;"
                )
                cursor.execute("INSERT INTO schema_migrations (name) VALUES (?)", (migration_rtn,))
            cursor.execute("PRAGMA table_info(projects);")
            project_cols = [r["name"] for r in cursor.fetchall()]
            if "instructions" not in project_cols:
                cursor.execute("ALTER TABLE projects ADD COLUMN instructions TEXT;")
            cursor.execute("PRAGMA table_info(project_nodes);")
            pn_cols = [r["name"] for r in cursor.fetchall()]
            if "instructions" not in pn_cols:
                cursor.execute("ALTER TABLE project_nodes ADD COLUMN instructions TEXT;")
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
        scheduled_at = _normalize_utc_iso(getattr(req, "scheduled_at", None))
        base_time = now
        if scheduled_at is not None:
            sched_dt = datetime.fromisoformat(scheduled_at.replace("Z", "+00:00"))
            if sched_dt > now:
                base_time = sched_dt
        expires_at = base_time + timedelta(seconds=req.expires_in_seconds)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO requests (
                    sender_id, recipient_id, question_type, prompt, context,
                    options, status, created_at, expires_at, updated_at, priority,
                    recipient_session_id, prompt_source, project_id, project_context,
                    scheduled_at, expires_in_seconds
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    req.project_id,
                    req.project_context,
                    scheduled_at,
                    req.expires_in_seconds,
                ),
            )
            req_id = cursor.lastrowid
            self._log(
                cursor, req_id, actor or f"agent:{req.sender_id}", "created",
                {"question_type": req.question_type.value, "expires_at": expires_at.isoformat()},
                at=now.isoformat(),
            )
            if scheduled_at:
                self._log(
                    cursor, req_id, actor or f"agent:{req.sender_id}", "scheduled",
                    {"scheduled_at": scheduled_at},
                    at=now.isoformat(),
                )
        return self.get_request(req_id)

    def get_request(self, request_id: int) -> Optional[RequestModel]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT r.*, p.name AS project_name
                FROM requests r
                LEFT JOIN projects p ON r.project_id = p.id
                WHERE r.id = ?
                """,
                (request_id,),
            )
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
        keys = row.keys()
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
            hold_alerted_at=_ts(row["hold_alerted_at"]),
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
            project_id=row["project_id"] if "project_id" in keys else None,
            project_name=row["project_name"] if "project_name" in keys else None,
            project_context=row["project_context"] if "project_context" in keys else None,
            relation_type_name=row["relation_type_name"] if "relation_type_name" in keys else None,
            progress_text=row["progress_text"] if "progress_text" in keys else None,
            progress_at=_ts(row["progress_at"]) if "progress_at" in keys else None,
            scheduled_at=_ts(row["scheduled_at"]) if "scheduled_at" in keys else None,
            expires_in_seconds=row["expires_in_seconds"] if "expires_in_seconds" in keys and row["expires_in_seconds"] else DEFAULT_EXPIRES_IN_SECONDS,
        )

    def open_request_toward(self, sender_session: str, recipient_id: str,
                            recipient_session: Optional[str]) -> Optional[int]:
        """
        RFC-16: the request that `sender_session` is running for the session
        (or, without one, the agent) it now wants to write to, if any. Its
        reply belongs in placet_reply, not in a new request.
        """
        sql = ("SELECT id FROM requests WHERE recipient_session_id = ?"
               " AND status IN ('APPROVED', 'DELIVERING', 'EXECUTING') AND ")
        if recipient_session:
            sql += "sender_session_id = ?"
            params = (sender_session, recipient_session)
        else:
            sql += "sender_id = ?"
            params = (sender_session, recipient_id)
        with self._get_connection() as conn:
            row = conn.execute(sql + " ORDER BY id DESC LIMIT 1", params).fetchone()
            return row["id"] if row else None

    def set_progress(self, request_id: int, text: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute("UPDATE requests SET progress_text = ?, progress_at = ?, updated_at = ? WHERE id = ?",
                         (text, now, now, request_id))

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
                SELECT r.*, p.name AS project_name,
                       s.id AS s_id, s.request_id AS s_request_id,
                       s.responder_id AS s_responder_id, s.content AS s_content,
                       s.created_at AS s_created_at, s.implicit AS s_implicit
                FROM requests r
                LEFT JOIN projects p ON r.project_id = p.id
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
        scheduled_at: Optional[Union[str, datetime]] = None,
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
        scheduled_at_iso = _normalize_utc_iso(scheduled_at) if scheduled_at is not None else None
        placeholders = ",".join("?" for _ in allowed_sources)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            new_expires_at = None
            if scheduled_at_iso is not None:
                cursor.execute(
                    "SELECT created_at, expires_at, scheduled_at, expires_in_seconds FROM requests WHERE id = ?",
                    (request_id,),
                )
                row = cursor.fetchone()
                if row:
                    secs = row["expires_in_seconds"] if "expires_in_seconds" in row.keys() and row["expires_in_seconds"] else None
                    if secs and secs >= 10:
                        window = timedelta(seconds=secs)
                    else:
                        try:
                            exp_dt = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
                            base_str = row["scheduled_at"] or row["created_at"]
                            base_dt = datetime.fromisoformat(base_str.replace("Z", "+00:00"))
                            window = exp_dt - base_dt
                            if window.total_seconds() < 10:
                                window = timedelta(seconds=DEFAULT_EXPIRES_IN_SECONDS)
                        except Exception:
                            window = timedelta(seconds=DEFAULT_EXPIRES_IN_SECONDS)
                    sched_dt = datetime.fromisoformat(scheduled_at_iso.replace("Z", "+00:00"))
                    now_dt = datetime.now(timezone.utc)
                    new_base = max(now_dt, sched_dt)
                    new_expires_at = (new_base + window).isoformat()

            cursor.execute(
                f"""
                UPDATE requests
                SET allow_auto_return = ?, approval = ?, status = ?, edited_prompt = ?, selected_options = ?, custom_text = ?,
                    decided_at = ?, updated_at = ?,
                    delivery_mode = COALESCE(?, delivery_mode),
                    recipient_session_id = COALESCE(?, recipient_session_id),
                    scheduled_at = COALESCE(?, scheduled_at),
                    expires_at = COALESCE(?, expires_at)
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
                    scheduled_at_iso,
                    new_expires_at,
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
            if scheduled_at_iso is not None:
                details["scheduled_at"] = scheduled_at_iso
                self._log(cursor, request_id, actor, "scheduled", {"scheduled_at": scheduled_at_iso}, at=now)
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

            req = self.get_request(request_id)
            if req and req.sender_session_id and req.sender_id != "human":
                sender_session = self.get_session(req.sender_session_id)
                sender_provider = sender_session.provider if sender_session else None
                cfg = self.usage_guard_config()
                if self.is_provider_over_limit(sender_provider, cfg["five_hour"], cfg["seven_day"], check_near_limit=cfg["enabled"]):
                    if cfg["deliver_at_reset"]:
                        reset_time = self.provider_reset_time(sender_provider, cfg["five_hour"], cfg["seven_day"], check_near_limit=cfg["enabled"])
                        if reset_time is not None:
                            scheduled_at_iso = _normalize_utc_iso(reset_time)
                            cursor.execute("UPDATE requests SET scheduled_at = ?, updated_at = ? WHERE id = ?",
                                           (scheduled_at_iso, now, request_id))
                            self._log(cursor, request_id, "system:limits", "scheduled",
                                      {"scheduled_at": scheduled_at_iso, "reason": "usage"}, at=now)
                        else:
                            cursor.execute("UPDATE requests SET allow_auto_return = 0, updated_at = ? WHERE id = ?",
                                           (now, request_id))
                            self._log(cursor, request_id, "system:limits", "automation_limited",
                                      {"reason": "usage", "provider": sender_provider}, at=now)
                    else:
                        cursor.execute("UPDATE requests SET allow_auto_return = 0, updated_at = ? WHERE id = ?",
                                       (now, request_id))
                        self._log(cursor, request_id, "system:limits", "automation_limited",
                                  {"reason": "usage", "provider": sender_provider}, at=now)
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
        extra_where: str = "",
        where_params: Sequence[Any] = (),
    ) -> bool:
        cursor.execute(
            f"UPDATE requests SET status = ?, updated_at = ?{extra_sql} WHERE id = ? AND status = ?{extra_where}",
            [target.value, now, *extra_params, request_id, source.value, *where_params],
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
                extra_where=" AND (scheduled_at IS NULL OR datetime(scheduled_at) <= datetime(?))",
                where_params=[now],
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

    def long_pauses(self, seconds: float = PAUSE_UNRESUMED_SECONDS) -> List[int]:
        """
        Executing requests whose turn paused (turn_paused) and was not resumed
        for `seconds`: flagged once like an unseen start, so the hold card
        shows and the held-queue push can fire. The queue stays held.
        """
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        cutoff = (now_dt - timedelta(seconds=seconds)).isoformat()
        last_event = (
            "(SELECT {col} FROM request_events e WHERE e.request_id = r.id"
            " AND e.event IN ('turn_paused', 'turn_resumed') ORDER BY e.id DESC LIMIT 1)"
        )
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT r.id FROM requests r WHERE r.status = ? AND r.start_unconfirmed_at IS NULL"
                f" AND r.queue_released_at IS NULL AND r.recipient_session_id IS NOT NULL"
                f" AND {last_event.format(col='e.event')} = 'turn_paused'"
                f" AND {last_event.format(col='e.at')} <= ? ORDER BY r.id",
                (RequestStatus.EXECUTING.value, cutoff),
            )
            ids = [r["id"] for r in cursor.fetchall()]
            for req_id in ids:
                cursor.execute("UPDATE requests SET start_unconfirmed_at = ? WHERE id = ?", (now, req_id))
                self._log(cursor, req_id, "system", "pause_unresumed", at=now)
            return ids

    def held_queues_to_alert(self, seconds: float = HOLD_ALERT_SECONDS) -> List[Tuple[int, str, int]]:
        """
        Requests holding a session's queue whose turn end is unseen (stalled,
        start unconfirmed, or answered) for `seconds`, with at least one
        approved message waiting behind them: marked once each, as
        (request id, session id, messages waiting). Mirrors holdsQueueUnseen.
        """
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        cutoff = (now_dt - timedelta(seconds=seconds)).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT r.id, r.recipient_session_id AS sid, (
                    SELECT COUNT(*) FROM requests w
                    WHERE w.recipient_session_id = r.recipient_session_id AND w.status = ?
                ) AS waiting
                FROM requests r
                WHERE r.hold_alerted_at IS NULL AND r.queue_released_at IS NULL
                  AND r.recipient_session_id IS NOT NULL
                  AND r.status IN (?, ?, ?)
                  AND COALESCE(r.execution_state, 'unobserved') IN ('unobserved', 'started')
                  AND COALESCE(
                        CASE WHEN r.status = ? THEN r.completed_at END,
                        r.start_unconfirmed_at,
                        CASE WHEN r.status = ? THEN r.updated_at END
                      ) <= ?
                  AND waiting > 0
                ORDER BY r.id
                """,
                (RequestStatus.APPROVED.value, RequestStatus.EXECUTING.value, RequestStatus.STALLED.value,
                 RequestStatus.COMPLETED.value, RequestStatus.COMPLETED.value, RequestStatus.STALLED.value,
                 cutoff),
            )
            rows = [(r["id"], r["sid"], r["waiting"]) for r in cursor.fetchall()]
            for req_id, _, _ in rows:
                cursor.execute("UPDATE requests SET hold_alerted_at = ? WHERE id = ?", (now, req_id))
                self._log(cursor, req_id, "system", "queue_hold_alerted", at=now)
            return rows

    def release_on_observed_turn_end(self, session_id: str) -> List[int]:
        """
        A turn end that is not a pause, or a new turn starting, was seen for
        this session: the answered requests still holding its queue ended
        with it. Evidence only, never elapsed time; a request without a reply
        is left to its own signals.
        """
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE requests SET queue_released_at = ? WHERE recipient_session_id = ?"
                " AND queue_released_at IS NULL AND status = ? AND completed_at IS NOT NULL AND completed_at <= ?"
                " AND COALESCE(execution_state, 'unobserved') IN ('unobserved', 'started')"
                " AND EXISTS (SELECT 1 FROM responses WHERE responses.request_id = requests.id)"
                " RETURNING id",
                (now, session_id, RequestStatus.COMPLETED.value, now),
            )
            ids = sorted(r["id"] for r in cursor.fetchall())
            for req_id in ids:
                self._log(cursor, req_id, "system", "released_on_observed_turn_end", at=now)
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
            # Exactly what request_in_flight holds on, delivery aside: a request
            # answered before its delivery settled holds the queue as well.
            cursor.execute(
                "UPDATE requests SET queue_released_at = ? WHERE id = ? AND queue_released_at IS NULL"
                " AND status IN (?, ?, ?) AND COALESCE(execution_state, 'unobserved') IN ('unobserved', 'started')",
                (now, request_id, RequestStatus.EXECUTING.value, RequestStatus.STALLED.value,
                 RequestStatus.COMPLETED.value),
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

    def schedule_request(
        self, request_id: int, scheduled_at: Optional[Union[str, datetime]], actor: str
    ) -> Optional[RequestModel]:
        """
        Schedules or clears the scheduled delivery time of a request (RF-65).
        Allowed only while the request is not yet delivered:
        status in (PENDING, APPROVED, COMPLETED), delivered_at IS NULL,
        and (delivery_state IS NULL OR delivery_state = 'waiting' OR status = 'COMPLETED').
        """
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        scheduled_at_iso = _normalize_utc_iso(scheduled_at) if scheduled_at is not None else None
        valid_statuses = (RequestStatus.PENDING.value, RequestStatus.APPROVED.value, RequestStatus.COMPLETED.value)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT created_at, expires_at, scheduled_at, expires_in_seconds FROM requests WHERE id = ?",
                (request_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            secs = row["expires_in_seconds"] if "expires_in_seconds" in row.keys() and row["expires_in_seconds"] else None
            if secs and secs >= 10:
                window = timedelta(seconds=secs)
            else:
                try:
                    exp_dt = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
                    base_str = row["scheduled_at"] or row["created_at"]
                    base_dt = datetime.fromisoformat(base_str.replace("Z", "+00:00"))
                    window = exp_dt - base_dt
                    if window.total_seconds() < 10:
                        window = timedelta(seconds=DEFAULT_EXPIRES_IN_SECONDS)
                except Exception:
                    window = timedelta(seconds=DEFAULT_EXPIRES_IN_SECONDS)

            if scheduled_at_iso is None:
                new_expires_at = (now + window).isoformat()
            else:
                sched_dt = datetime.fromisoformat(scheduled_at_iso.replace("Z", "+00:00"))
                new_base = max(now, sched_dt)
                new_expires_at = (new_base + window).isoformat()

            cursor.execute(
                f"""
                UPDATE requests
                SET scheduled_at = ?, expires_at = ?, updated_at = ?
                WHERE id = ?
                  AND delivered_at IS NULL
                  AND status IN ({','.join('?' for _ in valid_statuses)})
                  AND (delivery_state IS NULL OR delivery_state = 'waiting' OR status = ?)
                """,
                (
                    scheduled_at_iso,
                    new_expires_at,
                    now_iso,
                    request_id,
                    *valid_statuses,
                    RequestStatus.COMPLETED.value,
                ),
            )
            if cursor.rowcount == 0:
                return None
            if scheduled_at is None:
                self._log(cursor, request_id, actor, "schedule_cleared", at=now_iso)
            else:
                self._log(cursor, request_id, actor, "scheduled", {"scheduled_at": scheduled_at_iso}, at=now_iso)
        return self.get_request(request_id)

    def steer_candidates(self) -> List[Tuple[RequestModel, SessionModel]]:
        """'Now' messages for sessions in the middle of a turn that can take them."""
        now = datetime.now(timezone.utc).isoformat()
        placeholders = ", ".join("?" for _ in STEER_PROVIDERS)
        with self._get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT r.id AS request_id, s.id AS session_id FROM requests r
                JOIN sessions s ON s.id = r.recipient_session_id
                WHERE r.status = ? AND r.delivery_mode = 'now' AND s.state = 'busy'
                  AND s.ended_at IS NULL AND s.excluded_at IS NULL AND s.provider IN ({placeholders})
                  AND (r.scheduled_at IS NULL OR datetime(r.scheduled_at) <= datetime(?))
                ORDER BY r.id
                """,
                (RequestStatus.APPROVED.value, *STEER_PROVIDERS, now),
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
                extra_where=" AND (scheduled_at IS NULL OR datetime(scheduled_at) <= datetime(?))",
                where_params=[now],
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
        now = datetime.now(timezone.utc)
        for r in self.list_requests(status=RequestStatus.APPROVED, oldest_first=True, limit=200):
            if r.recipient_id == "human" or r.recipient_id in in_flight:
                continue
            if r.scheduled_at is not None and r.scheduled_at > now:
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
                "UPDATE requests SET delivered_at = ? WHERE id = ? AND delivered_at IS NULL AND (sender_id = 'human' OR recipient_id = 'human' OR status != 'COMPLETED' OR allow_auto_return = 1 OR return_decision IN ('approved', 'forwarded')) AND (scheduled_at IS NULL OR datetime(scheduled_at) <= datetime(?))",
                (now, request_id, now),
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
        now = datetime.now(timezone.utc).isoformat()
        later_ts = "CASE WHEN scheduled_at IS NOT NULL AND datetime(scheduled_at) > datetime(updated_at) THEN scheduled_at ELSE updated_at END"
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT id FROM requests
                WHERE delivered_at IS NULL AND status != ? AND (sender_id = 'human' OR recipient_id = 'human' OR status != 'COMPLETED' OR allow_auto_return = 1 OR return_decision IN ('approved', 'forwarded'))
                  -- the user's own messages: the outcome is in the conversation
                  AND sender_id != 'human'
                  AND ({later_ts}) IS NOT NULL
                  AND datetime({later_ts}) <= datetime(?)
                  AND datetime({later_ts}) > datetime(?)
                  AND (scheduled_at IS NULL OR datetime(scheduled_at) <= datetime(?))
                ORDER BY datetime({later_ts}) ASC
                LIMIT ?
                """,
                (
                    RequestStatus.PENDING.value,
                    settled_before.isoformat(),
                    settled_after.isoformat(),
                    now,
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
            continued_to=row["continued_to"] if "continued_to" in row.keys() else None,
            prompt_count=row["prompt_count"] if "prompt_count" in row.keys() else 0,
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

    def continue_session(self, old_session_id: str, new_session_id: str) -> bool:
        """
        RF-29d: Closes the previous session as continued, and migrates its
        Home pin and node to the new session.
        """
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            old = cursor.execute("SELECT * FROM sessions WHERE id = ?", (old_session_id,)).fetchone()
            new = cursor.execute("SELECT * FROM sessions WHERE id = ?", (new_session_id,)).fetchone()
            if old is None or new is None:
                return False
            if old["continued_to"] is not None:
                return False
            # Close old session and record continuation
            cursor.execute(
                "UPDATE sessions SET ended_at = COALESCE(ended_at, ?), continued_to = ? WHERE id = ?",
                (now, new_session_id, old_session_id),
            )
            # Migrate pin if old was pinned
            if old["pinned_at"] is not None:
                cursor.execute(
                    "UPDATE sessions SET pinned_at = ? WHERE id = ?",
                    (old["pinned_at"], new_session_id),
                )
                cursor.execute("UPDATE sessions SET pinned_at = NULL WHERE id = ?", (old_session_id,))
            # Migrate node if old was attached to one and new has none
            if old["node_id"] is not None and new["node_id"] is None:
                cursor.execute("UPDATE sessions SET node_id = ? WHERE id = ?", (old["node_id"], new_session_id))
                cursor.execute("UPDATE sessions SET node_id = NULL WHERE id = ?", (old_session_id,))
            return True

    def increment_prompt_count(self, session_id: str) -> None:
        with self._get_connection() as conn:
            conn.execute("UPDATE sessions SET prompt_count = prompt_count + 1 WHERE id = ?", (session_id,))

    # --- Nodes (RFC-02): stable named sessions -------------------------------

    @staticmethod
    def _row_to_node(row: sqlite3.Row) -> NodeModel:
        instructions = row["instructions"] if "instructions" in row.keys() else None
        return NodeModel(id=row["id"], name=row["name"], provider=row["provider"], cwd=row["cwd"],
                         created_at=datetime.fromisoformat(row["created_at"]),
                         session_id=row["session_id"],
                         instructions=instructions)

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
            rel_ids = [r["id"] for r in conn.execute("SELECT id FROM relations WHERE from_node = ? OR to_node = ?", (node_id, node_id))]
            if rel_ids:
                conn.execute(f"DELETE FROM project_relations WHERE relation_id IN ({','.join('?' for _ in rel_ids)})", rel_ids)
            conn.execute("DELETE FROM project_nodes WHERE node_id = ?", (node_id,))
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
            approval=row["approval"], reply_approval=row["reply_approval"],
            created_at=datetime.fromisoformat(row["created_at"]),
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

    def create_relation(self, from_node: int, to_node: int, type_id: int, approval: str,
                        reply_approval: Optional[str] = None) -> str | RelationModel:
        """The new relation, or "nodes" / "type" (not found), "self", "exists".
        The reply policy defaults to the outbound one (RFC-16)."""
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
                    "INSERT INTO relations (from_node, to_node, type_id, approval, reply_approval, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)", (from_node, to_node, type_id, approval, reply_approval or approval, now))
            except sqlite3.IntegrityError:
                return "exists"
            relation_id = cur.lastrowid
        return self.get_relation(relation_id)

    def set_relation_approval(self, relation_id: int, approval: str) -> bool:
        with self._get_connection() as conn:
            cur = conn.execute("UPDATE relations SET approval = ? WHERE id = ?", (approval, relation_id))
            return cur.rowcount > 0

    def set_relation_reply_approval(self, relation_id: int, reply_approval: str) -> bool:
        with self._get_connection() as conn:
            cur = conn.execute("UPDATE relations SET reply_approval = ? WHERE id = ?", (reply_approval, relation_id))
            return cur.rowcount > 0

    def delete_relation(self, relation_id: int) -> bool:
        with self._get_connection() as conn:
            conn.execute("DELETE FROM project_relations WHERE relation_id = ?", (relation_id,))
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
            type_name = None
            if relation_id is not None:
                rt_row = cur.execute(
                    "SELECT rt.name FROM relations r JOIN relation_types rt ON rt.id = r.type_id WHERE r.id = ?",
                    (relation_id,),
                ).fetchone()
                if rt_row:
                    type_name = rt_row["name"]
            cur.execute(
                "UPDATE requests SET recipient_id = COALESCE(?, recipient_id), relation_id = ?,"
                " relation_type_name = COALESCE(?, relation_type_name),"
                " recipient_session_id = ? WHERE id = ? AND status = 'PENDING'",
                (recipient_id, relation_id, type_name, recipient_session_id, request_id))
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
            type_name = None
            row = conn.execute(
                "SELECT rt.name FROM relations r JOIN relation_types rt ON rt.id = r.type_id WHERE r.id = ?",
                (relation_id,),
            ).fetchone()
            if row:
                type_name = row["name"]
            conn.execute(
                "UPDATE requests SET relation_id = ?, relation_type_name = COALESCE(?, relation_type_name) WHERE id = ?",
                (relation_id, type_name, request_id),
            )

    def set_request_project_context(self, request_id: int, project_context: Optional[str]) -> None:
        with self._get_connection() as conn:
            conn.execute("UPDATE requests SET project_context = ? WHERE id = ?", (project_context, request_id))

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

    def get_usage(self, provider: str) -> Optional[str]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT data FROM provider_usage WHERE provider = ?", (provider,)).fetchone()
            return row["data"] if row else None

    def record_usage_samples(
        self,
        provider: str,
        buckets: Any,
        observed_at: Optional[Any] = None,
        now: Optional[datetime] = None,
    ) -> bool:
        """
        RF-64b: record usage samples into usage_samples table when:
        1. No previous samples for this provider;
        2. >= 10 minutes elapsed since last sample;
        3. used_percent of any bucket changed by >= 1.0 percentage points;
        4. resets_at of any bucket changed (e.g. window reset).

        Also purges samples older than 400 days (throttled to at most once per hour).
        """
        if not buckets:
            return False

        bucket_items = []
        for b in buckets:
            if isinstance(b, dict):
                b_id = str(b.get("id", ""))
                b_group = b.get("group") or b.get("bucket_group")
                b_window = str(b.get("window", ""))
                b_used = float(b.get("used_percent", 0.0))
                raw_resets = b.get("resets_at")
            else:
                b_id = str(getattr(b, "id", ""))
                b_group = getattr(b, "group", None)
                b_window = str(getattr(b, "window", ""))
                b_used = float(getattr(b, "used_percent", 0.0))
                raw_resets = getattr(b, "resets_at", None)

            b_resets = _normalize_utc_iso(raw_resets)

            bucket_items.append({
                "id": b_id,
                "group": b_group,
                "window": b_window,
                "used_percent": b_used,
                "resets_at": b_resets,
            })

        observed_at_str = _normalize_utc_iso(observed_at or now or datetime.now(timezone.utc))
        try:
            observed_at_dt = datetime.fromisoformat(observed_at_str.replace("Z", "+00:00"))
        except Exception:
            observed_at_dt = now or datetime.now(timezone.utc)

        if observed_at_dt.tzinfo is None:
            observed_at_dt = observed_at_dt.replace(tzinfo=timezone.utc)

        with self._get_connection() as conn:
            last_row = conn.execute(
                "SELECT observed_at FROM usage_samples WHERE provider = ? ORDER BY observed_at DESC, id DESC LIMIT 1",
                (provider,),
            ).fetchone()

            should_record = False
            if last_row is None:
                should_record = True
            else:
                last_obs_str = last_row["observed_at"]
                try:
                    last_obs_dt = datetime.fromisoformat(last_obs_str.replace("Z", "+00:00"))
                    if last_obs_dt.tzinfo is None:
                        last_obs_dt = last_obs_dt.replace(tzinfo=timezone.utc)
                    elapsed_seconds = (observed_at_dt - last_obs_dt).total_seconds()
                except Exception:
                    elapsed_seconds = 600.0

                if elapsed_seconds >= 600.0:
                    should_record = True
                else:
                    prev_rows = conn.execute(
                        "SELECT bucket_id, bucket_group, used_percent, resets_at FROM usage_samples"
                        " WHERE provider = ? AND observed_at = ?",
                        (provider, last_obs_str),
                    ).fetchall()
                    prev_map = {(r["bucket_id"], r["bucket_group"]): r for r in prev_rows}

                    if len(prev_map) != len(bucket_items):
                        should_record = True
                    else:
                        for b in bucket_items:
                            key = (b["id"], b["group"])
                            if key not in prev_map:
                                should_record = True
                                break
                            prev_b = prev_map[key]
                            if abs(b["used_percent"] - prev_b["used_percent"]) >= 1.0 - 1e-9:
                                should_record = True
                                break
                            r1 = b["resets_at"]
                            r2 = prev_b["resets_at"]
                            if (r1 or None) != (r2 or None):
                                if not r1 or not r2:
                                    should_record = True
                                    break
                                try:
                                    dt1 = datetime.fromisoformat(r1.replace("Z", "+00:00"))
                                    dt2 = datetime.fromisoformat(r2.replace("Z", "+00:00"))
                                    if dt1 != dt2:
                                        should_record = True
                                        break
                                except Exception:
                                    if r1 != r2:
                                        should_record = True
                                        break

            if not should_record:
                return False

            for b in bucket_items:
                conn.execute(
                    "INSERT INTO usage_samples (provider, observed_at, window, bucket_id, bucket_group, used_percent, resets_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (provider, observed_at_str, b["window"], b["id"], b["group"], b["used_percent"], b["resets_at"]),
                )

            # Retention: purge samples older than 400 days (throttled once per hour)
            purge_now = now or observed_at_dt or datetime.now(timezone.utc)
            if purge_now.tzinfo is None:
                purge_now = purge_now.replace(tzinfo=timezone.utc)
            purge_ts = purge_now.timestamp()
            if abs(purge_ts - self._last_usage_purge_time) >= 3600.0:
                cutoff = purge_now - timedelta(days=400)
                conn.execute("DELETE FROM usage_samples WHERE observed_at < ?", (cutoff.isoformat(),))
                self._last_usage_purge_time = purge_ts

            return True

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

    def near_limit_providers(self, five_hour: float, seven_day: float,
                             now: Optional[datetime] = None) -> Set[str]:
        """
        Usage guard: providers whose 5h or 7d window is at or above the given
        percentage, including model groups (Placet does not know which model a
        conversation uses). Old readings of polled providers are unknown, not
        near the limit: Claude's need a valid login (automatic renewal).
        """
        from backend.app.models import ProviderUsage
        now = now or datetime.now(timezone.utc)
        threshold = {"5h": five_hour, "7d": seven_day}
        near = set()
        for data in self.list_usage():
            usage = ProviderUsage.model_validate_json(data)
            observed = usage.observed_at
            if observed is not None and observed.tzinfo is None:
                observed = observed.replace(tzinfo=timezone.utc)
            if (usage.provider in USAGE_POLLED and observed is not None
                    and now - observed > USAGE_STALE_AFTER):
                continue
            if any(b.used_percent >= threshold[b.window] and (b.resets_at is None or b.resets_at > now)
                   for b in usage.buckets):
                near.add(usage.provider)
        return near

    def is_provider_over_limit(
        self,
        provider: Optional[str],
        five_hour: float,
        seven_day: float,
        check_near_limit: bool = True,
        now: Optional[datetime] = None,
    ) -> bool:
        """RF-66: True if provider has used up account window (100%) or exceeded thresholds."""
        if not provider:
            return False
        now = now or datetime.now(timezone.utc)
        if provider in self.limited_providers(now=now):
            return True
        if check_near_limit and provider in self.near_limit_providers(five_hour, seven_day, now=now):
            return True
        return False

    def provider_reset_time(
        self,
        provider: Optional[str],
        five_hour: float,
        seven_day: float,
        check_near_limit: bool = True,
        now: Optional[datetime] = None,
    ) -> Optional[datetime]:
        """
        RF-66: Latest resets_at among the over-limit windows of provider.
        Returns None if reset time is unknown or stale (e.g. missing resets_at,
        resets_at <= now, or polled provider older than USAGE_STALE_AFTER).
        """
        if not provider:
            return None
        from backend.app.models import ProviderUsage
        now = now or datetime.now(timezone.utc)
        data = self.get_usage(provider)
        if not data:
            return None
        usage = ProviderUsage.model_validate_json(data)
        observed = usage.observed_at
        if observed is not None and observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        if (
            usage.provider in USAGE_POLLED
            and observed is not None
            and now - observed > USAGE_STALE_AFTER
        ):
            return None

        threshold = {"5h": five_hour, "7d": seven_day}
        over_limit_buckets = []
        for b in usage.buckets:
            if b.group is None and b.used_percent >= 100:
                over_limit_buckets.append(b)
            elif check_near_limit and b.used_percent >= threshold.get(b.window, 100):
                over_limit_buckets.append(b)

        if not over_limit_buckets:
            return None

        reset_times = []
        for b in over_limit_buckets:
            if b.resets_at is None:
                return None
            rt = b.resets_at
            if rt.tzinfo is None:
                rt = rt.replace(tzinfo=timezone.utc)
            if rt <= now:
                return None
            reset_times.append(rt)

        return max(reset_times)

    def get_stats(
        self,
        from_date: Any,
        to_date: Any,
        tz_name: str = "UTC",
        usage_thresholds: Optional[Dict[str, int]] = None,
    ) -> Dict[str, Any]:
        """
        RF-64: compute aggregate statistics for requests and provider usage
        in the range [from_date, to_date] localized to tz_name.
        """
        import zoneinfo

        tz = zoneinfo.ZoneInfo(tz_name)
        start_local = datetime(from_date.year, from_date.month, from_date.day, 0, 0, 0, tzinfo=tz)
        start_utc = start_local.astimezone(timezone.utc)
        end_local = datetime(to_date.year, to_date.month, to_date.day, 0, 0, 0, tzinfo=tz) + timedelta(days=1)
        end_utc = end_local.astimezone(timezone.utc)

        start_sql = (start_utc - timedelta(days=1)).isoformat()
        end_sql = (end_utc + timedelta(days=1)).isoformat()

        days = []
        cur = from_date
        while cur <= to_date:
            days.append(cur.isoformat())
            cur += timedelta(days=1)

        day_counts = {d: 0 for d in days}
        day_human_deltas: Dict[str, List[float]] = {d: [] for d in days}
        by_relation_type: Dict[str, int] = {}
        by_project_map: Dict[Tuple[Optional[int], str], int] = {}
        by_sender_provider = {"claude-code": 0, "gemini": 0, "codex": 0, "human": 0}
        by_recipient_provider = {"claude-code": 0, "gemini": 0, "codex": 0, "human": 0}
        approval = {"automatic": 0, "manual": 0, "ad_hoc": 0}
        decisions = {"approved": 0, "edited": 0, "rejected": 0, "expired": 0, "cancelled": 0}
        user_requests_count = 0
        user_replies_count = 0
        all_human_deltas: List[float] = []

        def resolve_provider(party_id: str, sess_provider: Optional[str]) -> str:
            if party_id == "human":
                return "human"
            if sess_provider:
                sp = sess_provider.lower()
                if "claude" in sp:
                    return "claude-code"
                if "gemini" in sp or "antigravity" in sp:
                    return "gemini"
                if "codex" in sp or "chatgpt" in sp:
                    return "codex"
                return sess_provider
            p = (party_id or "").lower()
            if "claude" in p:
                return "claude-code"
            if "gemini" in p or "antigravity" in p:
                return "gemini"
            if "codex" in p or "chatgpt" in p:
                return "codex"
            return party_id or "unknown"

        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT r.id, r.sender_id, r.recipient_id, r.question_type, r.prompt, r.status,
                       r.edited_prompt, r.created_at, r.decided_at, r.completed_at, r.approval,
                       r.relation_id, r.relation_type_name, r.project_id,
                       p.name AS project_name,
                       s_send.provider AS sender_session_provider,
                       s_rec.provider AS recipient_session_provider
                FROM requests r
                LEFT JOIN projects p ON p.id = r.project_id
                LEFT JOIN sessions s_send ON s_send.id = r.sender_session_id
                LEFT JOIN sessions s_rec ON s_rec.id = r.recipient_session_id
                WHERE r.created_at >= ? AND r.created_at <= ?
                ORDER BY r.created_at ASC
                """,
                (start_sql, end_sql),
            ).fetchall()

            in_range_count = 0
            for r in rows:
                try:
                    dt = datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if not (start_utc <= dt < end_utc):
                        continue
                except Exception:
                    continue

                in_range_count += 1
                local_date = dt.astimezone(tz).date().isoformat()
                if local_date in day_counts:
                    day_counts[local_date] += 1

                if r["recipient_id"] == "human" and r["decided_at"] is not None:
                    try:
                        decided_dt = datetime.fromisoformat(r["decided_at"].replace("Z", "+00:00"))
                        if decided_dt.tzinfo is None:
                            decided_dt = decided_dt.replace(tzinfo=timezone.utc)
                        delta_s = max(0.0, (decided_dt - dt).total_seconds())
                        if local_date in day_human_deltas:
                            day_human_deltas[local_date].append(delta_s)
                        all_human_deltas.append(delta_s)
                    except Exception:
                        pass

                rel_name = r["relation_type_name"]
                if rel_name:
                    by_relation_type[rel_name] = by_relation_type.get(rel_name, 0) + 1

                proj_id = r["project_id"]
                proj_name = r["project_name"] if proj_id is not None else "Senza progetto"
                proj_key = (proj_id, proj_name)
                by_project_map[proj_key] = by_project_map.get(proj_key, 0) + 1

                send_p = resolve_provider(r["sender_id"], r["sender_session_provider"])
                rec_p = resolve_provider(r["recipient_id"], r["recipient_session_provider"])
                by_sender_provider[send_p] = by_sender_provider.get(send_p, 0) + 1
                by_recipient_provider[rec_p] = by_recipient_provider.get(rec_p, 0) + 1

                if r["sender_id"] == "human":
                    approval["ad_hoc"] += 1
                elif r["approval"] == "automatic":
                    approval["automatic"] += 1
                elif r["approval"] == "manual":
                    approval["manual"] += 1

                st = r["status"]
                if st in ("APPROVED", "COMPLETED", "DELIVERING", "EXECUTING", "STALLED"):
                    if r["edited_prompt"]:
                        decisions["edited"] += 1
                    else:
                        decisions["approved"] += 1
                elif st == "REJECTED":
                    decisions["rejected"] += 1
                elif st == "EXPIRED":
                    decisions["expired"] += 1
                elif st == "CANCELLED":
                    decisions["cancelled"] += 1

                if r["sender_id"] == "human":
                    user_requests_count += 1
                if r["recipient_id"] == "human" and (r["decided_at"] is not None or st in ("APPROVED", "COMPLETED")):
                    user_replies_count += 1

            per_day = []
            for d in days:
                deltas = day_human_deltas[d]
                mean_s = round(sum(deltas) / len(deltas), 1) if deltas else None
                per_day.append({
                    "date": d,
                    "count": day_counts[d],
                    "mean_reply_seconds": mean_s,
                })

            by_project = [
                {"id": pid, "name": pname, "count": cnt}
                for (pid, pname), cnt in sorted(by_project_map.items(), key=lambda x: (-x[1], str(x[0][1])))
            ]

            mean_reply_s = round(sum(all_human_deltas) / len(all_human_deltas), 1) if all_human_deltas else None

            # Provider usage samples in period
            usage_guard_cfg = usage_thresholds or {"five_hour": 90, "seven_day": 95}
            sample_rows = conn.execute(
                """
                SELECT provider, observed_at, window, bucket_id, bucket_group, used_percent
                FROM usage_samples
                WHERE observed_at >= ? AND observed_at <= ?
                ORDER BY observed_at ASC
                """,
                (start_sql, end_sql),
            ).fetchall()

            buckets_data: Dict[Tuple[str, str, str, Optional[str]], Dict[str, List[float]]] = {}
            for s in sample_rows:
                try:
                    s_dt = datetime.fromisoformat(s["observed_at"].replace("Z", "+00:00"))
                    if s_dt.tzinfo is None:
                        s_dt = s_dt.replace(tzinfo=timezone.utc)
                    if not (start_utc <= s_dt < end_utc):
                        continue
                    s_date = s_dt.astimezone(tz).date().isoformat()
                    b_key = (s["provider"], s["window"], s["bucket_id"], s["bucket_group"])
                    if b_key not in buckets_data:
                        buckets_data[b_key] = {d: [] for d in days}
                    if s_date in buckets_data[b_key]:
                        buckets_data[b_key][s_date].append(s["used_percent"])
                except Exception:
                    continue

            usage_result = []
            for (prov, win, b_id, b_grp), day_dict in sorted(buckets_data.items()):
                thresh = usage_guard_cfg.get("five_hour", 90) if win == "5h" else usage_guard_cfg.get("seven_day", 95)
                peaks = []
                threshold_days = 0
                for d in days:
                    vals = day_dict.get(d, [])
                    peak_val = round(max(vals), 1) if vals else None
                    peaks.append({"date": d, "peak_used_percent": peak_val})
                    if peak_val is not None and peak_val >= thresh:
                        threshold_days += 1
                usage_result.append({
                    "provider": prov,
                    "window": win,
                    "bucket_id": b_id,
                    "bucket_group": b_grp,
                    "threshold": thresh,
                    "threshold_days": threshold_days,
                    "peaks": peaks,
                })

            return {
                "from": from_date.isoformat(),
                "to": to_date.isoformat(),
                "tz": tz_name,
                "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "total_requests": in_range_count,
                "per_day": per_day,
                "by_relation_type": by_relation_type,
                "by_project": by_project,
                "by_sender_provider": by_sender_provider,
                "by_recipient_provider": by_recipient_provider,
                "approval": approval,
                "decisions": decisions,
                "user_messages": {
                    "requests": user_requests_count,
                    "replies": user_replies_count,
                    "sent": user_requests_count,
                    "received": user_replies_count,
                },
                "mean_reply_seconds": mean_reply_s,
                "usage": usage_result,
            }

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
          -- RF-66: scheduled outcomes wait until scheduled_at arrives
          AND (scheduled_at IS NULL OR datetime(scheduled_at) <= datetime(?))
        ORDER BY updated_at ASC
    """
    _INJECTIONS_SQL = """
        SELECT id FROM requests
        WHERE status = ? AND recipient_id = ?
          -- the last parameter: 1 = only requests addressed to this very session
          AND (recipient_session_id = ? OR (recipient_session_id IS NULL AND ? = 0))
          -- a session never receives its own request (agent -> same agent)
          AND (sender_session_id IS NULL OR sender_session_id != ?)
          -- RF-65: scheduled messages are only delivered when scheduled_at arrives
          AND (scheduled_at IS NULL OR datetime(scheduled_at) <= datetime(?))
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
            now = datetime.now(timezone.utc).isoformat()
            cursor.execute(self._OUTCOMES_SQL, (session.id, RequestStatus.PENDING.value, now))
            outcome_ids = [r["id"] for r in cursor.fetchall()]
            cursor.execute(
                self._INJECTIONS_SQL,
                (RequestStatus.APPROVED.value, session.agent_id, session.id, int(explicit_only), session.id, now),
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
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(self._OUTCOMES_SQL, (session.id, RequestStatus.PENDING.value, now))
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
                (RequestStatus.APPROVED.value, session.agent_id, session.id, 0, session.id, now),
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
                    extra_where=" AND (scheduled_at IS NULL OR datetime(scheduled_at) <= datetime(?))",
                    where_params=[now],
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

    # --- Projects (RFC-09, RF-44) --------------------------------------------

    @staticmethod
    def _row_to_project(row: sqlite3.Row) -> ProjectModel:
        instructions = row["instructions"] if "instructions" in row.keys() else None
        return ProjectModel(
            id=row["id"],
            name=row["name"],
            description=row["description"] or "",
            relation_rule=row["relation_rule"],
            max_auto_hops=row["max_auto_hops"],
            max_auto_per_hour=row["max_auto_per_hour"],
            instructions=instructions,
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def create_project(
        self,
        name: str,
        description: str = "",
        relation_rule: Optional[str] = None,
        max_auto_hops: Optional[int] = None,
        max_auto_per_hour: Optional[int] = None,
        instructions: Optional[str] = None,
    ) -> ProjectModel:
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            try:
                cur = conn.execute(
                    "INSERT INTO projects (name, description, relation_rule, max_auto_hops, max_auto_per_hour, instructions, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (name.strip(), (description or "").strip(),
                     relation_rule.strip() if relation_rule else None,
                     max_auto_hops, max_auto_per_hour,
                     instructions.strip() if instructions and instructions.strip() else None,
                     now),
                )
            except sqlite3.IntegrityError:
                raise ProjectNameTaken(name) from None
            project_id = cur.lastrowid
            row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
            return self._row_to_project(row)

    def list_projects(self, node_id: Optional[int] = None) -> List[ProjectSummaryModel]:
        with self._get_connection() as conn:
            if node_id is not None:
                query = """
                SELECT p.*,
                  (SELECT COUNT(*) FROM project_nodes pn2 WHERE pn2.project_id = p.id) AS nodes_count,
                  (SELECT COUNT(*) FROM project_relations pr WHERE pr.project_id = p.id) AS relations_count,
                  (SELECT COUNT(*) FROM project_relations pr JOIN relations r ON r.id = pr.relation_id WHERE pr.project_id = p.id AND r.approval = 'automatic') AS automatic_relations_count
                FROM projects p
                JOIN project_nodes pn ON pn.project_id = p.id
                WHERE pn.node_id = ?
                ORDER BY p.name COLLATE NOCASE
                """
                rows = conn.execute(query, (node_id,)).fetchall()
            else:
                query = """
                SELECT p.*,
                  (SELECT COUNT(*) FROM project_nodes pn WHERE pn.project_id = p.id) AS nodes_count,
                  (SELECT COUNT(*) FROM project_relations pr WHERE pr.project_id = p.id) AS relations_count,
                  (SELECT COUNT(*) FROM project_relations pr JOIN relations r ON r.id = pr.relation_id WHERE pr.project_id = p.id AND r.approval = 'automatic') AS automatic_relations_count
                FROM projects p
                ORDER BY p.name COLLATE NOCASE
                """
                rows = conn.execute(query).fetchall()
            return [
                ProjectSummaryModel(
                    id=r["id"],
                    name=r["name"],
                    description=r["description"] or "",
                    relation_rule=r["relation_rule"],
                    max_auto_hops=r["max_auto_hops"],
                    max_auto_per_hour=r["max_auto_per_hour"],
                    instructions=r["instructions"] if "instructions" in r.keys() else None,
                    created_at=datetime.fromisoformat(r["created_at"]),
                    nodes_count=r["nodes_count"] or 0,
                    relations_count=r["relations_count"] or 0,
                    automatic_relations_count=r["automatic_relations_count"] or 0,
                )
                for r in rows
            ]

    def get_project(self, project_id: int) -> Optional[ProjectDetailModel]:
        with self._get_connection() as conn:
            p_row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
            if not p_row:
                return None

            node_rows = conn.execute(
                """
                SELECT nodes.*, pn.instructions,
                  (SELECT s.id FROM sessions s WHERE s.node_id = nodes.id
                   ORDER BY s.ended_at IS NOT NULL, s.last_seen_at DESC LIMIT 1) AS session_id
                FROM nodes
                JOIN project_nodes pn ON pn.node_id = nodes.id
                WHERE pn.project_id = ?
                ORDER BY nodes.name COLLATE NOCASE
                """,
                (project_id,),
            ).fetchall()
            nodes = [self._row_to_node(nr) for nr in node_rows]

            rel_rows = conn.execute(
                f"""
                {self._RELATIONS_SQL}
                JOIN project_relations pr ON pr.relation_id = r.id
                WHERE pr.project_id = ?
                ORDER BY f.name, t.name, y.name
                """,
                (project_id,),
            ).fetchall()

            relations = []
            for rr in rel_rows:
                rel_model = self._row_to_relation(rr)
                shared_rows = conn.execute(
                    """
                    SELECT p.name FROM projects p
                    JOIN project_relations pr ON pr.project_id = p.id
                    WHERE pr.relation_id = ? AND pr.project_id != ?
                    ORDER BY p.name COLLATE NOCASE
                    """,
                    (rel_model.id, project_id),
                ).fetchall()
                shared_with = [sr["name"] for sr in shared_rows]
                relations.append(ProjectRelationDetail(
                    **rel_model.model_dump(),
                    shared_with=shared_with,
                ))

            global_auto = self.get_automation()
            global_rule = (self.get_relation_rule() or "").strip()

            rule_parts = []
            if global_rule:
                rule_parts.append(global_rule)
            p_rule = (p_row["relation_rule"] or "").strip()
            if p_rule:
                rule_parts.append(f"[{p_row['name']}]\n{p_rule}")
            effective_rule = "\n\n".join(rule_parts) if rule_parts else None

            instructions = p_row["instructions"] if "instructions" in p_row.keys() else None
            return ProjectDetailModel(
                id=p_row["id"],
                name=p_row["name"],
                description=p_row["description"] or "",
                relation_rule=p_row["relation_rule"],
                max_auto_hops=p_row["max_auto_hops"],
                max_auto_per_hour=p_row["max_auto_per_hour"],
                instructions=instructions,
                created_at=datetime.fromisoformat(p_row["created_at"]),
                nodes=nodes,
                relations=relations,
                effective_rule=effective_rule,
                effective_max_hops=p_row["max_auto_hops"] if p_row["max_auto_hops"] is not None else global_auto["max_hops"],
                effective_max_per_hour=p_row["max_auto_per_hour"] if p_row["max_auto_per_hour"] is not None else global_auto["max_per_hour"],
            )

    def update_project(
        self,
        project_id: int,
        name: Optional[str] = None,
        description: Optional[str] = None,
        relation_rule: Optional[str] = None,
        max_auto_hops: Optional[int] = None,
        max_auto_per_hour: Optional[int] = None,
        instructions: Optional[str] = None,
        fields_set: Optional[Set[str]] = None,
    ) -> Optional[ProjectModel]:
        with self._get_connection() as conn:
            cur = conn.cursor()
            existing = cur.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
            if not existing:
                return None

            if fields_set is None:
                fields_set = set()
                if name is not None:
                    fields_set.add("name")
                if description is not None:
                    fields_set.add("description")
                if relation_rule is not None:
                    fields_set.add("relation_rule")
                if max_auto_hops is not None:
                    fields_set.add("max_auto_hops")
                if max_auto_per_hour is not None:
                    fields_set.add("max_auto_per_hour")
                if instructions is not None:
                    fields_set.add("instructions")

            updates = []
            params = []
            if "name" in fields_set:
                updates.append("name = ?")
                params.append(name.strip() if name else existing["name"])
            if "description" in fields_set:
                updates.append("description = ?")
                params.append(description.strip() if description else "")
            if "relation_rule" in fields_set:
                rule_val = relation_rule.strip() if (relation_rule and relation_rule.strip()) else None
                updates.append("relation_rule = ?")
                params.append(rule_val)
            if "max_auto_hops" in fields_set:
                updates.append("max_auto_hops = ?")
                params.append(max_auto_hops)
            if "max_auto_per_hour" in fields_set:
                updates.append("max_auto_per_hour = ?")
                params.append(max_auto_per_hour)
            if "instructions" in fields_set:
                inst_val = instructions.strip() if (instructions and instructions.strip()) else None
                updates.append("instructions = ?")
                params.append(inst_val)


            if updates:
                params.append(project_id)
                try:
                    cur.execute(
                        f"UPDATE projects SET {', '.join(updates)} WHERE id = ?",
                        params,
                    )
                except sqlite3.IntegrityError:
                    raise ProjectNameTaken(name or "") from None

            row = cur.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
            return self._row_to_project(row)

    def delete_project(self, project_id: int) -> bool:
        with self._get_connection() as conn:
            conn.execute("DELETE FROM project_nodes WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM project_relations WHERE project_id = ?", (project_id,))
            cur = conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            return cur.rowcount > 0

    def add_node_to_project(self, project_id: int, node_id: int) -> bool:
        with self._get_connection() as conn:
            if conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
                return False
            if conn.execute("SELECT 1 FROM nodes WHERE id = ?", (node_id,)).fetchone() is None:
                return False
            conn.execute(
                "INSERT OR IGNORE INTO project_nodes (project_id, node_id) VALUES (?, ?)",
                (project_id, node_id),
            )
            return True

    def remove_node_from_project(self, project_id: int, node_id: int) -> Tuple[bool, List[int]]:
        with self._get_connection() as conn:
            if conn.execute("SELECT 1 FROM project_nodes WHERE project_id = ? AND node_id = ?",
                            (project_id, node_id)).fetchone() is None:
                return False, []
            removed_rel_ids = [
                r["id"] for r in conn.execute(
                    """
                    SELECT r.id FROM relations r
                    JOIN project_relations pr ON pr.relation_id = r.id
                    WHERE pr.project_id = ? AND (r.from_node = ? OR r.to_node = ?)
                    """,
                    (project_id, node_id, node_id),
                ).fetchall()
            ]
            if removed_rel_ids:
                conn.execute(
                    f"DELETE FROM project_relations WHERE project_id = ? AND relation_id IN ({','.join('?' for _ in removed_rel_ids)})",
                    [project_id, *removed_rel_ids],
                )
            conn.execute("DELETE FROM project_nodes WHERE project_id = ? AND node_id = ?",
                         (project_id, node_id))
            return True, removed_rel_ids

    def add_relation_to_project(self, project_id: int, relation_id: int) -> Tuple[bool, List[str]]:
        with self._get_connection() as conn:
            if conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
                return False, []
            rel = conn.execute("SELECT from_node, to_node FROM relations WHERE id = ?",
                               (relation_id,)).fetchone()
            if rel is None:
                return False, []

            conn.execute("INSERT OR IGNORE INTO project_nodes (project_id, node_id) VALUES (?, ?)",
                         (project_id, rel["from_node"]))
            conn.execute("INSERT OR IGNORE INTO project_nodes (project_id, node_id) VALUES (?, ?)",
                         (project_id, rel["to_node"]))

            shared_rows = conn.execute(
                """
                SELECT p.name FROM projects p
                JOIN project_relations pr ON pr.project_id = p.id
                WHERE pr.relation_id = ? AND pr.project_id != ?
                ORDER BY p.name COLLATE NOCASE
                """,
                (relation_id, project_id),
            ).fetchall()
            shared_with = [r["name"] for r in shared_rows]

            conn.execute(
                "INSERT OR IGNORE INTO project_relations (project_id, relation_id) VALUES (?, ?)",
                (project_id, relation_id),
            )
            return True, shared_with

    def remove_relation_from_project(self, project_id: int, relation_id: int) -> bool:
        with self._get_connection() as conn:
            cur = conn.execute(
                "DELETE FROM project_relations WHERE project_id = ? AND relation_id = ?",
                (project_id, relation_id),
            )
            return cur.rowcount > 0

    def get_relation_effective_policy(self, relation_id: int) -> Dict[str, Any]:
        global_automation = self.get_automation()
        global_rule = (self.get_relation_rule() or "").strip()
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT p.id, p.name, p.relation_rule, p.max_auto_hops, p.max_auto_per_hour
                FROM projects p
                JOIN project_relations pr ON pr.project_id = p.id
                WHERE pr.relation_id = ?
                ORDER BY p.name COLLATE NOCASE
                """,
                (relation_id,),
            ).fetchall()

        if not rows:
            return {
                "projects": [],
                "rule": global_rule,
                "max_hops": global_automation["max_hops"],
                "max_per_hour": global_automation["max_per_hour"],
                "paused": global_automation["paused"],
            }

        project_names = [r["name"] for r in rows]
        effective_hops = min(
            r["max_auto_hops"] if r["max_auto_hops"] is not None else global_automation["max_hops"]
            for r in rows
        )
        effective_per_hour = min(
            r["max_auto_per_hour"] if r["max_auto_per_hour"] is not None else global_automation["max_per_hour"]
            for r in rows
        )

        rule_parts = []
        if global_rule:
            rule_parts.append(global_rule)
        for r in rows:
            p_rule = (r["relation_rule"] or "").strip()
            if p_rule:
                rule_parts.append(f"[{r['name']}]\n{p_rule}")

        effective_rule = "\n\n".join(rule_parts)

        return {
            "projects": project_names,
            "rule": effective_rule,
            "max_hops": effective_hops,
            "max_per_hour": effective_per_hour,
            "paused": global_automation["paused"],
        }

    # --- Project & Node Instructions (RFC-15) --------------------------------

    def update_project_node_instructions(self, project_id: int, node_id: int, instructions: Optional[str]) -> bool:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM project_nodes WHERE project_id = ? AND node_id = ?",
                (project_id, node_id),
            ).fetchone()
            if not row:
                return False
            val = instructions.strip() if instructions and instructions.strip() else None
            conn.execute(
                "UPDATE project_nodes SET instructions = ? WHERE project_id = ? AND node_id = ?",
                (val, project_id, node_id),
            )
            return True

    def get_project_node_instructions(self, project_id: int, node_id: int) -> Optional[str]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT instructions FROM project_nodes WHERE project_id = ? AND node_id = ?",
                (project_id, node_id),
            ).fetchone()
            return row["instructions"] if row else None

    def is_node_in_project(self, project_id: int, node_id: int) -> bool:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM project_nodes WHERE project_id = ? AND node_id = ?",
                (project_id, node_id),
            ).fetchone()
            return row is not None

    def list_node_project_outgoing_relations(self, project_id: int, node_id: int) -> List[dict]:
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT r.id, r.from_node, r.to_node, r.approval,
                       rt.name AS type_name,
                       tn.name AS to_name
                FROM relations r
                JOIN project_relations pr ON pr.relation_id = r.id AND pr.project_id = ?
                JOIN relation_types rt ON rt.id = r.type_id
                JOIN nodes tn ON tn.id = r.to_node
                WHERE r.from_node = ?
                ORDER BY rt.name COLLATE NOCASE ASC, tn.name COLLATE NOCASE ASC, r.id ASC
                """,
                (project_id, node_id),
            ).fetchall()
            return [dict(r) for r in rows]

    def list_projects_for_relation(self, relation_id: int) -> List[ProjectModel]:
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT p.* FROM projects p
                JOIN project_relations pr ON pr.project_id = p.id
                WHERE pr.relation_id = ?
                ORDER BY p.name COLLATE NOCASE ASC, p.id ASC
                """,
                (relation_id,),
            ).fetchall()
            return [self._row_to_project(r) for r in rows]


def compose_project_instructions(
    project_name: str,
    project_instructions: Optional[str],
    node_instructions: Optional[str],
    outgoing_relations: List[dict],
) -> str:
    """Pure function to compose RFC-15 project and node instructions block."""
    sections = []
    p_inst = (project_instructions or "").strip()
    if p_inst:
        sections.append(f"[Placet · Project {project_name}]\n{p_inst}")

    n_inst = (node_instructions or "").strip()
    if n_inst:
        sections.append(f"[Placet · Your role in {project_name}]\n{n_inst}")

    if outgoing_relations:
        sorted_rels = sorted(
            outgoing_relations,
            key=lambda r: (
                (r.get("type_name") or "").lower(),
                (r.get("to_name") or "").lower(),
                r.get("id") or 0,
            ),
        )
        rel_lines = [
            f"- {r['type_name']} → {r['to_name']} ({r['approval']})"
            for r in sorted_rels
        ]
        sections.append(f"[Placet · Your relations in {project_name}]\n" + "\n".join(rel_lines))

    return "\n".join(sections) if sections else ""

