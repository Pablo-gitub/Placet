import os
import subprocess

import pytest

from admin_executor.web_bootstrap import copy_runtime, dependency_command, main, validate_origin, reviewed_snapshot


def test_preview_cannot_start_dependencies_or_services(monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("Preview executed a command"))
    main(["--origin", "https://placet-admin.example.ts.net"])
    assert "Preview only" in capsys.readouterr().out


def test_bundle_manifest_is_parsed_without_executing_checkout_code(tmp_path):
    (tmp_path / "bootstrap.py").write_text('MODULES = ("bootstrap.py",)\nraise RuntimeError("must not execute")\n')
    files, digest = reviewed_snapshot(tmp_path)
    assert list(files) == ["bootstrap.py"]
    assert len(digest) == 64


@pytest.mark.parametrize("origin", ["http://placet-admin.example.ts.net", "https://pwa.example.ts.net",
    "https://placet-admin.example.ts.net:443", "https://placet-admin.example.ts.net/path",
    "https://placet-admin.example.ts.net#fragment", "https://user@placet-admin.example.ts.net"])
def test_origin_requires_exact_dedicated_service_hostname(origin):
    with pytest.raises(ValueError):
        validate_origin(origin)


def test_dependency_build_has_no_root_pip_or_terminal_inheritance():
    command = dependency_command()
    assert "--property=DynamicUser=yes" in command
    assert "--property=ProtectHome=yes" in command
    assert "--property=KillMode=control-group" in command
    assert "--property=StandardInput=null" in command
    assert "--wait" in command and "--pipe" not in command and "--pty" not in command
    assert command[-1].endswith("web_dependencies.py")


def test_runtime_copy_rejects_links_and_preserves_only_safe_modes(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    binary = source / "python"
    binary.write_bytes(b"not executed")
    binary.chmod(0o777)
    link = source / "escape"
    link.symlink_to("/etc/passwd")
    with pytest.raises(ValueError, match="links"):
        copy_runtime(source, target)
    assert not target.exists()
    link.unlink()
    copy_runtime(source, target)
    assert (target / "python").stat().st_mode & 0o7777 == 0o755
    assert (target / "python").read_bytes() == b"not executed"
    with pytest.raises(FileExistsError):
        copy_runtime(source, target)


def test_apply_without_reviewed_digest_does_not_write(monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    with pytest.raises(SystemExit) as error:
        main(["--origin", "https://placet-admin.example.ts.net", "--apply"])
    assert error.value.code == 2


def test_system_python_runtime_can_be_relocated_without_executing_source(tmp_path):
    source, target = tmp_path / "source", tmp_path / "protected"
    subprocess.run(["/usr/bin/python3", "-I", "-m", "venv", "--copies", "--without-pip", str(source)], check=True)
    alias = source / "lib64"
    if alias.is_symlink() and os.readlink(alias) == "lib":
        alias.unlink()
    copy_runtime(source, target)
    result = subprocess.run([str(target / "bin/python"), "-I", "-c", "import sys; print(sys.prefix)"],
                            capture_output=True, text=True, check=True)
    assert result.stdout.strip() == str(target)
