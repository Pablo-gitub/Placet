import os
import subprocess

import pytest

from admin_executor.serve import main as serve
from admin_executor.terminal import main as terminal
from admin_executor.web_service import UNIT


def test_http_process_and_enrollment_cli_enforce_opposite_privileges(monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    with pytest.raises(ValueError, match="unprivileged"):
        serve()
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    with pytest.raises(SystemExit) as error:
        terminal(["begin-enrollment"])
    assert error.value.code == 2


def test_web_service_cannot_reach_private_credentials_or_network_state():
    assert "User=placet-admin-web" in UNIT
    assert "PrivateNetwork=yes" in UNIT
    assert "RestrictAddressFamilies=AF_UNIX" in UNIT
    assert "InaccessiblePaths=/var/lib/placet-admin /var/lib/placet-admin-tailscale /run/placet-admin-tailscale" in UNIT
    assert "RuntimeDirectoryMode=0710" in UNIT
    assert "StateDirectoryMode=0700" in UNIT
    assert "ExecStart=/usr/bin/env -i " in UNIT


@pytest.mark.skipif(os.environ.get("PLACET_TEST_SYSTEMD") != "1", reason="Explicit host parser integration check")
def test_web_unit_parser(tmp_path):
    path = tmp_path / "placet-admin-web.service"
    path.write_text(UNIT)
    result = subprocess.run(["/usr/bin/systemd-analyze", "verify", "--man=no", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
