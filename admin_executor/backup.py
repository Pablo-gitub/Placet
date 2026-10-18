"""Consistent SQLite backups, executed as the data-owning service user.

The future supervisor must quiesce writes for the migration cutover and acquire
this backup into protected storage. No restore or automatic rollback is offered.
"""
import os
import sqlite3
import tempfile
import time
from contextlib import closing
from pathlib import Path


def backup_sqlite(source: Path, target: Path, *, timeout_seconds: float = 30):
    if os.geteuid() == 0:
        raise ValueError("Database backup must run as the unprivileged data owner")
    if timeout_seconds <= 0:
        raise ValueError("Backup timeout must be positive")
    if source.is_symlink() or not source.is_file():
        raise ValueError("Expected a regular source database")
    if target.parent.is_symlink() or not target.parent.is_dir():
        raise ValueError("Expected a protected backup directory")
    if os.path.lexists(target):
        raise FileExistsError(target)
    fd, temporary = tempfile.mkstemp(prefix=".backup-", dir=target.parent)
    os.close(fd)
    staged = Path(temporary)
    deadline = time.monotonic() + timeout_seconds

    def progress(_status, _remaining, _total):
        if time.monotonic() >= deadline:
            raise TimeoutError("SQLite backup timed out")

    try:
        with closing(sqlite3.connect(source.absolute().as_uri() + "?mode=ro", uri=True, timeout=1)) as original:
            with closing(sqlite3.connect(staged)) as destination:
                original.backup(destination, pages=128, progress=progress, sleep=0.05)
                destination.execute("PRAGMA journal_mode=DELETE")
                if destination.execute("PRAGMA quick_check").fetchone() != ("ok",):
                    raise ValueError("SQLite backup failed its integrity check")
        fd = os.open(staged, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        # Hard-link publication is atomic and refuses any existing target. Both
        # paths are regular files on the same filesystem in a trusted directory.
        os.link(staged, target, follow_symlinks=False)
        fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        staged.unlink(missing_ok=True)
        for suffix in ("-wal", "-shm", "-journal"):
            Path(str(staged) + suffix).unlink(missing_ok=True)
