"""Bootstrap preview and offline installation never touch host services."""
import json
import os
from pathlib import Path

import pytest

from admin_executor.bootstrap import CODE, CONFIG, STATE, MODULES, install, main, snapshot


@pytest.fixture
def fake_root(tmp_path):
    for parent in (CODE.parent, CONFIG.parent, STATE.parent):
        (tmp_path / parent).mkdir(parents=True, exist_ok=True)
    for path in tmp_path.rglob("*"):
        if path.is_dir():
            path.chmod(0o755)
    return tmp_path


def bundle():
    return snapshot(Path(__file__).resolve().parents[1] / "admin_executor")[0]


def test_preview_has_no_side_effects(capsys):
    main([])
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "preview"
    assert len(result["bundle_sha256"]) == 64
    assert result["actions_enabled"] is result["services_started"] is False


def test_offline_install_is_protected_and_disabled(fake_root):
    files = bundle()
    install(files, root=fake_root, owner=os.getuid())
    for name in MODULES:
        assert (fake_root / CODE / "admin_executor" / name).read_bytes() == files[name]
    catalogue = json.loads((fake_root / CONFIG / "catalogue.json").read_text())
    assert len(catalogue["actions"]) == 3
    assert all(action["enabled"] is False for action in catalogue["actions"])
    assert (fake_root / CODE).stat().st_mode & 0o777 == 0o755
    for path in (CONFIG, STATE, STATE / "releases", STATE / "jobs", STATE / "backups"):
        assert (fake_root / path).stat().st_mode & 0o777 == 0o700
    with pytest.raises(FileExistsError):
        install(files, root=fake_root, owner=os.getuid())


def test_changed_bundle_requires_new_review(tmp_path):
    for name in MODULES:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("# first\n")
    _, initial = snapshot(tmp_path)
    (tmp_path / MODULES[0]).write_text("# changed\n")
    _, changed = snapshot(tmp_path)
    assert changed != initial
    (tmp_path / MODULES[0]).unlink()
    (tmp_path / MODULES[0]).symlink_to(tmp_path / MODULES[1])
    with pytest.raises(OSError):
        snapshot(tmp_path)


def test_unsafe_parent_refused_before_any_write(fake_root):
    (fake_root / "etc").chmod(0o777)
    with pytest.raises(ValueError, match="Unsafe administrative"):
        install(bundle(), root=fake_root, owner=os.getuid())
    assert not (fake_root / CODE).exists()
    assert not (fake_root / STATE).exists()


def test_existing_path_and_symlink_refused_before_any_write(fake_root):
    (fake_root / STATE).symlink_to(fake_root / "etc", target_is_directory=True)
    with pytest.raises(FileExistsError):
        install(bundle(), root=fake_root, owner=os.getuid())
    assert not (fake_root / CODE).exists()


def test_cli_refuses_apply_without_matching_digest(monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    with pytest.raises(SystemExit) as error:
        main(["--apply", "--bundle-sha256", "0" * 64])
    assert error.value.code == 2


def test_cli_refuses_unprivileged_apply(monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    with pytest.raises(SystemExit) as error:
        main(["--apply"])
    assert error.value.code == 2
