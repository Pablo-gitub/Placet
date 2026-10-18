"""Hostile build output cannot escape acquisition or become partially visible."""
import hashlib
import io
import json
import tarfile

import pytest

from admin_executor.artifacts import Limits, acquire


COMMIT = "a" * 40


def archive_of(*members):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w") as tar:
        for name, kind, body, mode in members:
            item = tarfile.TarInfo(name)
            item.type, item.mode = kind, mode
            item.linkname = "../../outside"
            item.size = len(body) if kind == tarfile.REGTYPE else 0
            tar.addfile(item, io.BytesIO(body))
    return data.getvalue()


def run(data, releases, **kwargs):
    return acquire(io.BytesIO(data), releases, COMMIT, hashlib.sha256(data).hexdigest(), **kwargs)


def test_snapshot_manifest_and_existing_release(tmp_path):
    data = archive_of(("backend/app.py", tarfile.REGTYPE, b"print('not executed')", 0o777),
                      ("frontend/index.html", tarfile.REGTYPE, b"hello", 0o666))
    target = run(data, tmp_path)
    manifest = json.loads((target / "release-manifest.json").read_text())
    assert manifest["commit"] == COMMIT
    assert manifest["archive_sha256"] == hashlib.sha256(data).hexdigest()
    assert manifest["files"][0]["sha256"] == hashlib.sha256(b"print('not executed')").hexdigest()
    assert (target / "backend/app.py").stat().st_mode & 0o7777 == 0o755
    assert (target / "frontend/index.html").stat().st_mode & 0o7777 == 0o644
    with pytest.raises(FileExistsError):
        run(data, tmp_path)
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("name,kind,mode", [
    ("../outside", tarfile.REGTYPE, 0o644),
    ("/outside", tarfile.REGTYPE, 0o644),
    ("a/../outside", tarfile.REGTYPE, 0o644),
    ("a//b", tarfile.REGTYPE, 0o644),
    ("a\\b", tarfile.REGTYPE, 0o644),
    ("link", tarfile.SYMTYPE, 0o644),
    ("link", tarfile.LNKTYPE, 0o644),
    ("device", tarfile.CHRTYPE, 0o644),
    ("pipe", tarfile.FIFOTYPE, 0o644),
    ("binary", tarfile.REGTYPE, 0o4755),
    ("release-manifest.json", tarfile.REGTYPE, 0o644),
])
def test_rejects_unsafe_entries_without_publishing(tmp_path, name, kind, mode):
    data = archive_of(("valid", tarfile.REGTYPE, b"first", 0o644), (name, kind, b"bad", mode))
    with pytest.raises(ValueError):
        run(data, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_rejects_duplicates_and_file_directory_conflicts(tmp_path):
    for second in ("file", "file/child"):
        data = archive_of(("file", tarfile.REGTYPE, b"first", 0o644),
                          (second, tarfile.REGTYPE, b"second", 0o644))
        with pytest.raises((ValueError, FileExistsError)):
            run(data, tmp_path)
        assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("limits", [
    Limits(archive_bytes=10), Limits(expanded_bytes=3),
    Limits(file_bytes=3), Limits(members=1), Limits(path_bytes=2),
])
def test_resource_limits_leave_no_release(tmp_path, limits):
    data = archive_of(("one", tarfile.REGTYPE, b"1234", 0o644),
                      ("two", tarfile.REGTYPE, b"1234", 0o644))
    with pytest.raises(ValueError):
        run(data, tmp_path, limits=limits)
    assert list(tmp_path.iterdir()) == []


def test_digest_mismatch_and_truncation(tmp_path):
    data = archive_of(("one", tarfile.REGTYPE, b"1234", 0o644))
    with pytest.raises(ValueError, match="digest mismatch"):
        acquire(io.BytesIO(data), tmp_path, COMMIT, "0" * 64)
    with pytest.raises((ValueError, tarfile.TarError)):
        run(data[:513], tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_symlink_release_parent_is_refused(tmp_path):
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="protected releases"):
        run(archive_of(("one", tarfile.REGTYPE, b"x", 0o644)), link)
