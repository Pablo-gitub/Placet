import os
import sqlite3

import pytest

from admin_executor.backup import backup_sqlite


def test_wal_backup_contains_committed_data_and_does_not_overwrite(tmp_path):
    source, target = tmp_path / "live.sqlite", tmp_path / "backup.sqlite"
    with sqlite3.connect(source) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE example(value TEXT)")
        db.execute("INSERT INTO example VALUES ('committed')")
        db.commit()
        backup_sqlite(source, target)
        with sqlite3.connect(target) as copy:
            assert copy.execute("SELECT value FROM example").fetchall() == [("committed",)]
        with pytest.raises(FileExistsError):
            backup_sqlite(source, target)
    assert target.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob(".backup-*"))


def test_corrupt_database_is_not_published(tmp_path):
    source, target = tmp_path / "bad.sqlite", tmp_path / "backup.sqlite"
    source.write_bytes(b"not a database")
    with pytest.raises(sqlite3.DatabaseError):
        backup_sqlite(source, target)
    assert not target.exists()
    assert not list(tmp_path.glob(".backup-*"))


def test_backup_refuses_root_and_symlinks(tmp_path, monkeypatch):
    source, target = tmp_path / "live.sqlite", tmp_path / "backup.sqlite"
    source.symlink_to("/etc/passwd")
    with pytest.raises(ValueError, match="regular source"):
        backup_sqlite(source, target)
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    with pytest.raises(ValueError, match="unprivileged"):
        backup_sqlite(source, target)
