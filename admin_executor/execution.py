"""Durable, serialized dispatch of already authorized administrative jobs.

This is an offline execution core, not an authorization endpoint. Production
callers must protect the database, lock and catalogue from all application UIDs.
Only the supervisor may update execution tables or run their recovery transition.
"""
from contextlib import closing
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time

from .authorization import confirmation
from .restart import RestartResult


STATES = ('running', 'succeeded', 'failed', 'outcome_unknown')
ACTIONS = frozenset(('restart-backend', 'restart-bridge'))


class ExecutionStore:
    def __init__(self, path):
        self.path = Path(path)
        if not self.path.is_file():
            raise ValueError('Existing authorization database required')
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('PRAGMA foreign_keys=ON')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS execution_jobs (
                    job_id TEXT PRIMARY KEY REFERENCES authorized_jobs(id),
                    state TEXT NOT NULL CHECK(state IN ('running','succeeded','failed','outcome_unknown')),
                    claimed_at REAL NOT NULL, finished_at REAL, result_code TEXT);
                CREATE TABLE IF NOT EXISTS execution_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES execution_jobs(job_id),
                    timestamp REAL NOT NULL, code TEXT NOT NULL);
            ''')

    def status(self, job_id):
        """Return only public result codes, never assertions or credentials."""
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute('SELECT state, claimed_at, finished_at, result_code FROM execution_jobs WHERE job_id=?',
                             (job_id,)).fetchone()
            if row is None:
                return None
            events = db.execute('SELECT sequence,timestamp,code FROM execution_events WHERE job_id=? ORDER BY sequence',
                                (job_id,)).fetchall()
        return dict(job_id=job_id, state=row[0], claimed_at=row[1], finished_at=row[2], result_code=row[3],
                    events=[dict(sequence=e[0], timestamp=e[1], code=e[2]) for e in events])

    def reconcile(self, job_id, outcome):
        """Record a human terminal decision; never run or retry an effect.

        The installed CLI is root-only and validates the protected database.
        Sharing the supervisor lock prevents reconciling a live invocation.
        """
        if (not isinstance(job_id, str) or len(job_id) != 32
                or any(c not in '0123456789abcdef' for c in job_id)
                or outcome not in ('succeeded', 'failed')):
            raise ValueError('A complete job ID and terminal outcome are required')
        fd = os.open(self.path.with_suffix('.execution.lock'),
                     os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('A supervisor is active; wait before reconciliation') from None
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT state,result_code FROM execution_jobs WHERE job_id=?', (job_id,)).fetchone()
                code = 'operator_reconciled_' + outcome
                if row == (outcome, code):
                    return self.status(job_id)
                if row is None or row[0] not in ('running', 'outcome_unknown'):
                    raise ValueError('Only an uncertain or abandoned job can be reconciled')
                now = time.time()
                db.execute('UPDATE execution_jobs SET state=?,finished_at=?,result_code=? WHERE job_id=?',
                           (outcome, now, code, job_id))
                db.execute('INSERT INTO execution_events(job_id,timestamp,code) VALUES (?,?,?)', (job_id, now, code))
            return self.status(job_id)
        finally:
            os.close(fd)

    def _recover_and_claim(self):
        # Called only while holding the exclusive supervisor lock, including
        # across the external effect. A second process cannot recover a live job.
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            now = time.time()
            running = db.execute("SELECT job_id FROM execution_jobs WHERE state='running'").fetchall()
            for (job_id,) in running:
                db.execute("UPDATE execution_jobs SET state='outcome_unknown',finished_at=?,result_code='interrupted' WHERE job_id=?",
                           (now, job_id))
                db.execute('INSERT INTO execution_events(job_id,timestamp,code) VALUES (?,?,?)',
                           (job_id, now, 'interrupted'))
            # A timed-out manager request may still be taking effect. Preserve
            # serialization across uncertain outcomes, not just live processes.
            # Only root-terminal reconciliation clears this gate.
            if db.execute("SELECT 1 FROM execution_jobs WHERE state='outcome_unknown' LIMIT 1").fetchone():
                return None
            row = db.execute('''SELECT a.id,a.operation,a.digest FROM authorized_jobs a
                WHERE a.state='authorized' AND NOT EXISTS
                (SELECT 1 FROM execution_jobs e WHERE e.job_id=a.id) ORDER BY a.rowid LIMIT 1''').fetchone()
            if row is None:
                return None
            db.execute("INSERT INTO execution_jobs VALUES (?, 'running', ?, NULL, NULL)", (row[0], now))
            db.execute('INSERT INTO execution_events(job_id,timestamp,code) VALUES (?,?,?)', (row[0], now, 'claimed'))
            return row

    def _finish(self, job_id, state, code):
        if state not in STATES or state == 'running':
            raise ValueError('Invalid terminal execution state')
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("UPDATE execution_jobs SET state=?,finished_at=?,result_code=? WHERE job_id=? AND state='running'",
                          (state, time.time(), code, job_id)).rowcount != 1:
                raise ValueError('Job is not running')
            db.execute('INSERT INTO execution_events(job_id,timestamp,code) VALUES (?,?,?)', (job_id, time.time(), code))
        return self.status(job_id)


class Supervisor:
    """One effect at most per authorization. Never retry ambiguous outcomes.

    The callable and enabled set are trusted construction-time dependencies,
    not request parameters. The installed entry point will load protected config.
    """
    def __init__(self, store, *, enabled, handler):
        self.store, self.enabled, self.handler = store, frozenset(enabled), handler

    def run_once(self):
        fd = os.open(self.store.path.with_suffix('.execution.lock'),
                     os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return None
            row = self.store._recover_and_claim()
            if row is None:
                return None
            job_id, operation, digest = row
            try:
                if hashlib.sha256(operation.encode()).hexdigest() != digest:
                    raise ValueError('Digest mismatch')
                parsed = json.loads(operation)
                proposal = parsed['proposal']
                action = proposal['action']
                if action not in ACTIONS or action not in self.enabled or proposal['parameters'] != {}:
                    raise ValueError('Action is not enabled')
                expected = confirmation(action, {}, installed_commit=parsed['installed_commit'],
                                        artifact_sha256=parsed['artifact_sha256'], summary=parsed['summary'])
                if expected != operation:
                    raise ValueError('Changed operation')
            except (ValueError, KeyError, TypeError):
                return self.store._finish(job_id, 'failed', 'operation_rejected')
            try:
                outcome = self.handler(action)
            except Exception:
                # No exception text or subprocess output crosses the boundary.
                outcome = RestartResult('outcome_unknown', 'handler_failed')
            if isinstance(outcome, RestartResult):
                try:
                    valid = RestartResult(str(outcome), outcome.code)
                    state, code = str(valid), valid.code
                except (ValueError, AttributeError):
                    state, code = 'outcome_unknown', 'invalid_result'
            elif type(outcome) is str and outcome in ('succeeded', 'failed', 'outcome_unknown'):
                # Existing durable tests/handlers may return legacy states.
                state, code = outcome, outcome
            else:
                state, code = 'outcome_unknown', 'invalid_result'
            return self.store._finish(job_id, state, code)
        finally:
            os.close(fd)
