import hashlib
import os
import subprocess

import pytest

from admin_executor.network_bootstrap import UNIT, UNIT_NAME, install_unit, main


def test_preview_does_not_run_commands(capsys, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("Preview started a command"))
    main([])
    output = capsys.readouterr().out
    assert hashlib.sha256(UNIT.encode()).hexdigest() in output
    assert "Preview only" in output
    assert "RuntimeDirectoryMode=0700" in UNIT
    assert "--tun=userspace-networking" in UNIT
    assert "--socket=/run/placet-admin-tailscale/tailscaled.sock" in UNIT
    assert "--statedir=/var/lib/placet-admin-tailscale" in UNIT


def test_fresh_install_refuses_replacement_and_insecure_parents(tmp_path):
    directory = tmp_path / "etc/systemd/system"
    directory.mkdir(parents=True)
    for path in (directory, directory.parent, directory.parent.parent):
        path.chmod(0o755)
    install_unit(tmp_path, owner=os.getuid())
    assert (directory / UNIT_NAME).read_text() == UNIT
    with pytest.raises(FileExistsError):
        install_unit(tmp_path, owner=os.getuid())
    (directory / UNIT_NAME).unlink()
    directory.chmod(0o777)
    with pytest.raises(ValueError, match="Unsafe systemd"):
        install_unit(tmp_path, owner=os.getuid())


def test_apply_rejects_missing_digest_before_install(monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    with pytest.raises(SystemExit) as error:
        main(["--apply"])
    assert error.value.code == 2


@pytest.mark.skipif(os.environ.get("PLACET_TEST_SYSTEMD") != "1", reason="Explicit host parser integration check")
def test_transport_unit_host_parser(tmp_path):
    path = tmp_path / UNIT_NAME
    path.write_text(UNIT)
    result = subprocess.run(["/usr/bin/systemd-analyze", "verify", "--man=no", str(path)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
