"""Strict proposals and durable deduplication, independent of the application DB.

This module deliberately cannot authorize or execute a proposal. The future
protected service must own the database and verify WebAuthn before creating
executable jobs. A proposal is untrusted input, never an execution permit.
"""
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


CATALOGUE_REVISION = 1
PROPOSAL_TTL_SECONDS = 15 * 60
CATALOGUE = {
    "deploy": frozenset({"commit"}),
    "restart-backend": frozenset(),
    "restart-bridge": frozenset(),
}


def canonical_proposal(action: str, parameters: dict) -> str:
    """Reject free commands, moving refs and unexpected arguments."""
    if not isinstance(action, str) or action not in CATALOGUE:
        raise ValueError("Unknown reserved action")
    if not isinstance(parameters, dict) or set(parameters) != CATALOGUE[action]:
        raise ValueError("Unexpected action parameters")
    if action == "deploy":
        commit = parameters["commit"]
        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError("A full lowercase commit SHA is required")
    return json.dumps({"action": action, "parameters": parameters,
                       "catalogue_revision": CATALOGUE_REVISION},
                      sort_keys=True, separators=(",", ":"))


class ProposalStore:
    """Durable intent only. Database location is administrative configuration.

    The caller supplies a request ID to survive retries. Reusing an ID for a
    different action fails. Repeating an action with a new ID is a new intent.
    Production file permissions must be provided by the protected bootstrap.
    """

    def __init__(self, path: Path):
        self.path = path
        with sqlite3.connect(path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS proposals (
                request_id TEXT PRIMARY KEY, canonical TEXT NOT NULL,
                digest TEXT NOT NULL, created_at TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state = 'awaiting_confirmation')
            )""")

    def propose(self, request_id: str, action: str, parameters: dict) -> dict:
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id):
            raise ValueError("Invalid request ID")
        canonical = canonical_proposal(action, parameters)
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""INSERT OR IGNORE INTO proposals VALUES (?, ?, ?, ?, ?)""",
                       (request_id, canonical, digest,
                        datetime.now(timezone.utc).isoformat(), "awaiting_confirmation"))
            row = db.execute("SELECT canonical, digest, created_at, state FROM proposals WHERE request_id = ?",
                             (request_id,)).fetchone()
            if row[0] != canonical:
                raise ValueError("Request ID already belongs to a different proposal")
        return {"request_id": request_id, "proposal": json.loads(row[0]),
                "digest": row[1], "created_at": row[2], "state": row[3]}
