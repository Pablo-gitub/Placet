"""Preserve the failed install while repairing only its disabled bootstrap phase."""
import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from admin_executor import web_bootstrap as setup
from admin_executor import web_dependencies as worker


@pytest.fixture
def partial(tmp_path, monkeypatch):
    for name, relative in (("PREFIX", "code"), ("VENV", "runtime"), ("UNIT", "web.service"),
                           ("NETWORK_DROPIN", "network.conf"), ("CONFIG", "config"), ("STATE", "state"),
                           ("WEB_STATE", "web-state"), ("BUILD_ROOT", "build-state")):
        monkeypatch.setattr(setup, name, tmp_path / relative)
    package = setup.PREFIX / "admin_executor"
    package.mkdir(parents=True)
    (package / "bootstrap.py").write_text('MODULES = ("bootstrap.py", "example.py")\n')
    (package / "example.py").write_text("# previous reviewed code\n")
    for file in package.iterdir():
        file.chmod(0o644)
    setup.CONFIG.mkdir()
    (setup.CONFIG / "origin").write_text("https://placet-admin.example.ts.net\n")
    (setup.CONFIG / "catalogue.json").write_text(json.dumps({"revision": 1, "actions": [
        {"id": action, "enabled": False} for action in ("deploy", "restart-backend", "restart-bridge")]}))
    for file in setup.CONFIG.iterdir():
        file.chmod(0o600)
    setup.BUILD_ROOT.mkdir()
    old_build = setup.BUILD_ROOT / "venv"
    old_build.mkdir()
    (old_build / "diagnostic-marker").write_text("preserve")
    monkeypatch.setattr(setup.pwd, "getpwnam", lambda _: SimpleNamespace(
        pw_uid=994, pw_gid=978, pw_dir="/var/lib/placet-admin-web", pw_shell="/usr/sbin/nologin", pw_name="placet-admin-web"))
    monkeypatch.setattr(setup.os, "getgrouplist", lambda *_: [978])
    files, digest = setup.reviewed_snapshot(package)
    new_files = {**files, "example.py": b"# corrected reviewed code\n"}
    return package, files, digest, new_files


def test_recovery_archives_old_code_and_preserves_failed_build(partial):
    package, old, digest, new = partial
    setup.replace_disabled_package(new, digest, origin="https://placet-admin.example.ts.net", root_owner=os.getuid())
    assert (package / "example.py").read_bytes() == new["example.py"]
    backup = setup.PREFIX / ("admin_executor.before-" + digest)
    assert (backup / "example.py").read_bytes() == old["example.py"]
    assert (setup.BUILD_ROOT / "venv/diagnostic-marker").read_text() == "preserve"
    assert not setup.VENV.exists() and not setup.UNIT.exists()


@pytest.mark.parametrize("reason", ["hash", "origin", "enabled", "later-phase"])
def test_recovery_refuses_unknown_or_active_installation(partial, reason):
    package, old, digest, new = partial
    origin = "https://placet-admin.example.ts.net"
    if reason == "hash":
        digest = "0" * 64
    elif reason == "origin":
        origin = "https://placet-admin.other.ts.net"
    elif reason == "enabled":
        (setup.CONFIG / "catalogue.json").write_text('{"revision":1,"actions":[]}')
    else:
        setup.UNIT.write_text("existing service")
    with pytest.raises(ValueError):
        setup.replace_disabled_package(new, digest, origin=origin, root_owner=os.getuid())
    assert (package / "example.py").read_bytes() == old["example.py"]
    assert not list(setup.PREFIX.glob("admin_executor.before-*"))


def test_dependency_interpreter_runs_only_in_private_tmp_and_old_state_is_preserved(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "ROOT", tmp_path / "state")
    worker.ROOT.mkdir()
    old = worker.ROOT / "venv"
    old.mkdir()
    (old / "marker").write_text("old failed state")
    real_run = subprocess.run
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if "pip" in args:
            assert args[0].startswith("/tmp/placet-admin-deps-")
            return subprocess.CompletedProcess(args, 0)
        return real_run(args, **kwargs)

    monkeypatch.setattr(worker.subprocess, "run", run)
    worker.main()
    assert (worker.ROOT / "venv-ready/bin/python").is_file()
    assert (old / "marker").read_text() == "old failed state"
    assert len(calls) == 2
    with pytest.raises(FileExistsError):
        worker.main()
