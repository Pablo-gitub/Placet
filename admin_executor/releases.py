"""Journalled release selection; no service execution, migration or authorization.

All paths and the database must belong to a protected administrative root.
Only the future trusted executor may call this module after authorization.
An acquired release must remain immutable. Tests use an ordinary temporary
directory; this library does not install the production ownership boundary.
"""
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path


SHA = re.compile(r"[0-9a-f]{40}")
JOB = re.compile(r"[A-Za-z0-9_-]{1,128}")


class ReleaseManager:
    """Persist intent before changing a pointer, reconcile instead of retrying.

    `current` is deliberately separate from /opt/placet. Selecting a release is
    not deployment success: a separate, future supervisor must back up data,
    coordinate services, run migrations unprivileged and verify health. No
    automatic rollback is allowed because database changes may be irreversible.
    """

    def __init__(self, root: Path):
        self.root = root.absolute()
        if root.is_symlink() or not root.is_dir():
            raise ValueError("Administrative root must be an existing directory")
        self.releases = self.root / "releases"
        if self.releases.is_symlink() or not self.releases.is_dir():
            raise ValueError("A protected releases directory is required")
        self.database = self.root / "activation.sqlite"
        with self._locked(), sqlite3.connect(self.database) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS activations (
                job_id TEXT PRIMARY KEY, previous TEXT, target TEXT NOT NULL,
                manifest_digest TEXT NOT NULL, state TEXT NOT NULL
                CHECK(state IN ('prepared', 'selected', 'verified', 'needs_review'))
            )""")

    @contextmanager
    def _locked(self):
        # Directory is trusted configuration; agents/builders must not be able
        # to replace this file (or the database) with a symlink.
        fd = os.open(self.root / "activation.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def _current(self):
        path = self.root / "current"
        if not os.path.lexists(path):
            return None
        if not path.is_symlink():
            raise ValueError("Current release must be a managed symlink")
        target = os.readlink(path)
        parts = target.split("/")
        if len(parts) != 2 or parts[0] != "releases" or not SHA.fullmatch(parts[1]):
            raise ValueError("Unexpected current release pointer")
        return parts[1]

    def _manifest_digest(self, commit):
        path = self.releases / commit
        manifest = path / "release-manifest.json"
        if path.is_symlink() or not path.is_dir() or manifest.is_symlink() or not manifest.is_file():
            raise ValueError("Release has not been acquired")
        # Acquisition bounds the manifest. A malformed or mismatching release
        # is refused before touching the current pointer.
        data = manifest.read_bytes()
        parsed = json.loads(data)
        if parsed.get("commit") != commit or not parsed.get("files"):
            raise ValueError("Release manifest does not match the requested commit")
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def _row(db, job_id):
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM activations WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown activation job")
        return dict(row)

    def prepare(self, job_id: str, commit: str, *, expected_current=None):
        if not isinstance(job_id, str) or not JOB.fullmatch(job_id):
            raise ValueError("Invalid activation job ID")
        if not isinstance(commit, str) or not SHA.fullmatch(commit):
            raise ValueError("A full commit SHA is required")
        if expected_current is not None and (not isinstance(expected_current, str) or not SHA.fullmatch(expected_current)):
            raise ValueError("Invalid expected current release")
        with self._locked(), sqlite3.connect(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT 1 FROM activations WHERE job_id = ?", (job_id,)).fetchone()
            if existing:
                row = self._row(db, job_id)
                if row["target"] != commit or row["previous"] != expected_current:
                    raise ValueError("Activation job ID already has different parameters")
                return row
            if db.execute("SELECT 1 FROM activations WHERE state != 'verified'").fetchone():
                raise ValueError("Another activation requires completion or review")
            current = self._current()
            if current != expected_current:
                raise ValueError("Installed release changed; new confirmation required")
            if current == commit:
                raise ValueError("Release is already selected")
            digest = self._manifest_digest(commit)
            db.execute("INSERT INTO activations VALUES (?, ?, ?, ?, 'prepared')",
                       (job_id, current, commit, digest))
            return self._row(db, job_id)

    def select(self, job_id):
        """Change the pointer once. A retry only reconciles the existing intent."""
        with self._locked(), sqlite3.connect(self.database) as db:
            row = self._row(db, job_id)
            if row["state"] != "prepared":
                return self._reconcile(db, row)
            current = self._current()
            if current == row["target"]:
                # Crash after rename, before updating SQLite: do not repeat.
                return self._reconcile(db, row)
            if current != row["previous"] or self._manifest_digest(row["target"]) != row["manifest_digest"]:
                db.execute("UPDATE activations SET state = 'needs_review' WHERE job_id = ?", (job_id,))
                return self._row(db, job_id)
            temporary = self.root / (".current-" + uuid.uuid4().hex)
            try:
                temporary.symlink_to("releases/" + row["target"])
                os.replace(temporary, self.root / "current")
                fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            finally:
                temporary.unlink(missing_ok=True)
            db.execute("UPDATE activations SET state = 'selected' WHERE job_id = ?", (job_id,))
            return self._row(db, job_id)

    def _reconcile(self, db, row):
        current = self._current()
        try:
            valid = current == row["target"] and self._manifest_digest(row["target"]) == row["manifest_digest"]
        except (OSError, ValueError, AttributeError):
            valid = False
        state = row["state"]
        if state == "prepared" and valid:
            state = "selected"
        elif state in ("selected", "verified") and not valid:
            state = "needs_review"
        elif state == "prepared" and current != row["previous"]:
            state = "needs_review"
        db.execute("UPDATE activations SET state = ? WHERE job_id = ?", (state, row["job_id"]))
        return self._row(db, row["job_id"])

    def status(self, job_id):
        with self._locked(), sqlite3.connect(self.database) as db:
            return self._reconcile(db, self._row(db, job_id))

    def record_health(self, job_id, *, healthy: bool):
        """Trusted supervisor's result, never an agent/backend-supplied field.

        Health checks and service supervision are not implemented here.
        """
        if type(healthy) is not bool:
            raise ValueError("Health result must be a boolean")
        with self._locked(), sqlite3.connect(self.database) as db:
            row = self._reconcile(db, self._row(db, job_id))
            if row["state"] != "selected":
                raise ValueError("Activation is not awaiting a health check")
            db.execute("UPDATE activations SET state = ? WHERE job_id = ?",
                       ("verified" if healthy else "needs_review", job_id))
            return self._row(db, job_id)
