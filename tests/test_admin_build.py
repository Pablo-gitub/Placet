"""Sandbox policy and hostile tree checks; not a live isolation attestation."""
import os
import subprocess

import pytest

from admin_executor.build import build_unit
from admin_executor.build_worker import main, regular_tree


def test_fixed_unit_has_no_shell_or_desktop_mounts():
    unit = build_unit("a" * 32)
    for protection in ("User=placet-build", "PrivateNetwork=yes", "ProtectHome=yes",
                       "NoNewPrivileges=yes", "KillMode=control-group", "Restart=no",
                       "RootDirectory=/var/lib/placet-admin/toolchain", "TasksMax=128"):
        assert protection in unit
    assert "EnvironmentFile=" not in unit
    assert "/bin/sh" not in unit and "/home/" not in unit
    assert "ExecStart=/usr/bin/env -i " in unit


@pytest.mark.skipif(os.environ.get("PLACET_TEST_SYSTEMD") != "1", reason="Explicit host parser integration check")
def test_host_systemd_parser(tmp_path):
    # The host parser checks unit syntax without installing or starting it.
    path = tmp_path / "placet-build-test.service"
    path.write_text(build_unit("a" * 32))
    result = subprocess.run(["/usr/bin/systemd-analyze", "verify", "--man=no", str(path)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("job", ["", "../test", "a" * 32 + "\nUser=root", "A" * 32, "$(id)"])
def test_job_cannot_inject_unit_directives(job):
    with pytest.raises(ValueError):
        build_unit(job)


def test_link_and_special_permission_bits_are_rejected(tmp_path):
    path = tmp_path / "bad"
    path.symlink_to("/etc/passwd")
    with pytest.raises(ValueError):
        regular_tree(tmp_path)
    path.unlink()
    path.write_text("data")
    path.chmod(0o4755)
    with pytest.raises(ValueError):
        regular_tree(tmp_path)


def test_worker_cannot_run_as_root_or_on_desktop(monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    with pytest.raises(ValueError, match="never run as root"):
        main()
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    with pytest.raises(ValueError, match="not ready"):
        main()
