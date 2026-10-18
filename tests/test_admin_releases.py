"""Offline activation recovery: never execute code or restart a real service."""
import hashlib
import io
import sqlite3
import tarfile

import pytest

from admin_executor.artifacts import acquire
from admin_executor.releases import ReleaseManager


FIRST, SECOND, THIRD = "a" * 40, "b" * 40, "c" * 40


@pytest.fixture
def releases(tmp_path):
    directory = tmp_path / "releases"
    directory.mkdir()
    for commit in (FIRST, SECOND, THIRD):
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w") as tar:
            info = tarfile.TarInfo("run_server.py")
            info.size = 4
            tar.addfile(info, io.BytesIO(b"test"))
        raw = data.getvalue()
        acquire(io.BytesIO(raw), directory, commit, hashlib.sha256(raw).hexdigest())
    return tmp_path, ReleaseManager(tmp_path)


def test_selection_requires_health_before_another_deploy(releases):
    root, manager = releases
    assert manager.prepare("job-1", FIRST)["state"] == "prepared"
    assert not (root / "current").exists()
    assert manager.select("job-1")["state"] == "selected"
    assert (root / "current").readlink().as_posix() == "releases/" + FIRST
    with pytest.raises(ValueError, match="requires completion"):
        manager.prepare("job-2", SECOND, expected_current=FIRST)
    assert manager.record_health("job-1", healthy=True)["state"] == "verified"
    assert manager.prepare("job-2", SECOND, expected_current=FIRST)["state"] == "prepared"
    assert manager.select("job-2")["state"] == "selected"


def test_retries_and_restart_do_not_repeat_selection(releases):
    root, manager = releases
    intent = manager.prepare("job-1", FIRST)
    assert manager.prepare("job-1", FIRST) == intent
    with pytest.raises(ValueError, match="different parameters"):
        manager.prepare("job-1", SECOND)
    assert manager.select("job-1") == ReleaseManager(root).select("job-1")


def test_crash_after_rename_reconciles_without_repeating(releases):
    root, manager = releases
    manager.prepare("job-1", FIRST)
    # Model the durable pointer rename followed by process death before the
    # SQLite state update. Recovery must notice the selected version.
    (root / "current").symlink_to("releases/" + FIRST)
    recovered = ReleaseManager(root)
    assert recovered.status("job-1")["state"] == "selected"
    assert recovered.select("job-1")["state"] == "selected"


def test_stale_current_requires_new_confirmation(releases):
    root, manager = releases
    (root / "current").symlink_to("releases/" + FIRST)
    with pytest.raises(ValueError, match="new confirmation"):
        manager.prepare("job-1", SECOND)
    manager.prepare("job-1", SECOND, expected_current=FIRST)
    (root / "current").unlink()
    (root / "current").symlink_to("releases/" + THIRD)
    assert manager.select("job-1")["state"] == "needs_review"
    assert (root / "current").readlink().as_posix() == "releases/" + THIRD


def test_modified_manifest_is_refused_before_selection(releases):
    root, manager = releases
    manager.prepare("job-1", FIRST)
    manifest = root / "releases" / FIRST / "release-manifest.json"
    manifest.write_text(manifest.read_text() + "\n")
    assert manager.select("job-1")["state"] == "needs_review"
    assert not (root / "current").exists()


def test_missing_manifest_after_selection_requires_review(releases):
    root, manager = releases
    manager.prepare("job-1", FIRST)
    manager.select("job-1")
    (root / "releases" / FIRST / "release-manifest.json").unlink()
    assert manager.status("job-1")["state"] == "needs_review"


def test_failed_health_never_rolls_back_files_or_data(releases):
    root, manager = releases
    manager.prepare("job-1", FIRST)
    manager.select("job-1")
    assert manager.record_health("job-1", healthy=False)["state"] == "needs_review"
    assert (root / "current").readlink().as_posix() == "releases/" + FIRST
    with pytest.raises(ValueError, match="requires completion"):
        manager.prepare("job-2", SECOND, expected_current=FIRST)


def test_invalid_pointer_and_unacquired_release_fail_closed(releases):
    root, manager = releases
    with pytest.raises(ValueError, match="not been acquired"):
        manager.prepare("job-1", "d" * 40)
    (root / "current").symlink_to("/tmp/unmanaged")
    with pytest.raises(ValueError, match="Unexpected current"):
        manager.prepare("job-1", FIRST)
    with sqlite3.connect(root / "activation.sqlite") as db:
        assert db.execute("SELECT COUNT(*) FROM activations").fetchone()[0] == 0


def test_no_success_can_be_recorded_before_selection(releases):
    _, manager = releases
    manager.prepare("job-1", FIRST)
    with pytest.raises(ValueError, match="not awaiting"):
        manager.record_health("job-1", healthy=True)
    assert manager.status("job-1")["state"] == "prepared"
