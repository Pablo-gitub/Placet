"""RS-09: the session credential is a PWA setting; the bridge mirrors it to a flag file the hook can stat."""
import os
import stat
from unittest.mock import MagicMock

import pytest

from bridge.bridge_daemon import BridgeDaemon
from bridge.flags import FLAG_NAME, mirror_flag
from bridge.stores import LocalStore, Work
from hooks import claude_code

placet_common = claude_code.placet_common          # the module the hook itself imports (hooks/ is on its path)
from tests.helpers import agent_client, human_client
from tests.test_session_messages import setup  # noqa: F401


# --- backend -----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_setting_is_off_by_default_and_the_human_turns_it_on(setup):
    app, db = setup
    async with human_client(app) as human:
        assert (await human.get("/api/settings/session-credentials")).json() == {"enabled": False}
        assert (await human.put("/api/settings/session-credentials", json={"enabled": True})).json() == {"enabled": True}
        assert (await human.get("/api/settings/session-credentials")).json() == {"enabled": True}
        assert (await human.put("/api/settings/session-credentials", json={"enabled": False})).json() == {"enabled": False}


@pytest.mark.asyncio
async def test_only_the_human_reads_or_changes_it(setup):
    app, db = setup
    for token in ("tok-claude", "tok-bridge"):
        async with agent_client(app, token) as client:
            assert (await client.get("/api/settings/session-credentials")).status_code == 403
            assert (await client.put("/api/settings/session-credentials", json={"enabled": True})).status_code == 403
    assert db.get_setting("session_credentials") != "true"


@pytest.mark.asyncio
async def test_the_bridge_work_carries_it_as_a_boolean(setup):
    app, db = setup
    async with agent_client(app, "tok-bridge") as bridge:
        assert (await bridge.get("/api/bridge/work")).json()["session_credentials"] is False
    async with human_client(app) as human:
        await human.put("/api/settings/session-credentials", json={"enabled": True})
    async with agent_client(app, "tok-bridge") as bridge:
        assert (await bridge.get("/api/bridge/work")).json()["session_credentials"] is True
    assert LocalStore(db).work().session_credentials is True
    assert Work().session_credentials is False


# --- bridge ------------------------------------------------------------------------------------


@pytest.fixture
def flags(tmp_path):
    return tmp_path / "placet" / "flags"


def test_the_bridge_writes_the_flag_privately_and_removes_it_when_off(flags):
    mirror_flag(True, flags)
    path = flags / FLAG_NAME
    assert stat.S_IMODE(os.stat(flags).st_mode) == 0o700 and stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert os.stat(path).st_uid == os.getuid() and path.is_file() and not path.is_symlink()
    assert sorted(p.name for p in flags.iterdir()) == [FLAG_NAME]          # nothing else, no temporary file left
    mirror_flag(True, flags)                                               # idempotent
    assert sorted(p.name for p in flags.iterdir()) == [FLAG_NAME]
    mirror_flag(False, flags)
    assert list(flags.iterdir()) == []
    mirror_flag(False, flags)                                              # nothing to remove: no error


def test_the_flag_is_never_written_through_a_link_or_a_loose_directory(tmp_path, flags):
    target = tmp_path / "elsewhere"
    target.mkdir()
    flags.parent.mkdir(parents=True)
    flags.symlink_to(target)                                               # the directory is a link
    mirror_flag(True, flags)
    assert list(target.iterdir()) == []
    flags.unlink()
    flags.mkdir(mode=0o777)
    os.chmod(flags, 0o777)                                                 # writable by others
    mirror_flag(True, flags)
    assert list(flags.iterdir()) == []


def test_a_link_in_place_of_the_flag_is_replaced_not_followed(tmp_path, flags):
    mirror_flag(True, flags)
    target = tmp_path / "victim"
    target.write_text("keep")
    (flags / FLAG_NAME).unlink()
    (flags / FLAG_NAME).symlink_to(target)
    mirror_flag(True, flags)
    assert target.read_text() == "keep" and (flags / FLAG_NAME).is_file() and not (flags / FLAG_NAME).is_symlink()
    (flags / FLAG_NAME).unlink()
    (flags / FLAG_NAME).symlink_to(target)
    mirror_flag(False, flags)
    assert target.read_text() == "keep" and not (flags / FLAG_NAME).exists() and not (flags / FLAG_NAME).is_symlink()


def test_every_work_tick_mirrors_the_setting(flags):
    store = MagicMock()
    store.work.return_value = Work(session_credentials=True)
    daemon = BridgeDaemon(store=store, tmux=MagicMock(), channels={}, claude_waker=MagicMock(), flags_dir=flags)
    daemon.run_loop_step()
    assert (flags / FLAG_NAME).is_file()
    store.work.return_value = Work(session_credentials=False)
    daemon.run_loop_step()
    assert not (flags / FLAG_NAME).exists()


def test_a_flag_that_cannot_be_written_never_breaks_the_tick(tmp_path):
    store = MagicMock()
    store.work.return_value = Work(session_credentials=True)
    blocker = tmp_path / "file"
    blocker.write_text("x")                                                # a file where the directory should be
    daemon = BridgeDaemon(store=store, tmux=MagicMock(), channels={}, claude_waker=MagicMock(), flags_dir=blocker / "flags")
    daemon.run_loop_step()


# --- hook --------------------------------------------------------------------------------------


def start_payload():
    return claude_code.build_event({"hook_event_name": "SessionStart", "source": "startup", "cwd": "/p"})


@pytest.fixture
def hook(monkeypatch, flags):
    monkeypatch.delenv("PLACET_SESSION_CREDENTIALS", raising=False)
    monkeypatch.setattr(placet_common, "FLAGS_DIR", flags)
    return flags


def test_the_hook_issues_a_credential_when_the_flag_is_there(hook):
    assert claude_code.issue_credential(start_payload()) is None           # no flag, no variable: off
    mirror_flag(True, hook)
    assert claude_code.issue_credential(start_payload()).startswith("plst_")
    mirror_flag(False, hook)
    assert claude_code.issue_credential(start_payload()) is None


def test_the_variable_overrides_the_flag_both_ways(hook, monkeypatch):
    monkeypatch.setenv("PLACET_SESSION_CREDENTIALS", "1")
    assert claude_code.issue_credential(start_payload()) is not None       # on without a flag
    mirror_flag(True, hook)
    monkeypatch.setenv("PLACET_SESSION_CREDENTIALS", "0")
    assert claude_code.issue_credential(start_payload()) is None           # "0" forces it off


def test_the_hook_refuses_a_link_a_foreign_file_and_a_loose_directory(hook, tmp_path, monkeypatch):
    mirror_flag(True, hook)
    real = hook / FLAG_NAME
    assert placet_common.flag_on(FLAG_NAME) is True
    mine = os.getuid()
    monkeypatch.setattr(placet_common.os, "getuid", lambda: mine + 1)
    assert placet_common.flag_on(FLAG_NAME) is False                       # not owned by this user
    monkeypatch.setattr(placet_common.os, "getuid", lambda: mine)
    real.unlink()
    (tmp_path / "decoy").write_text("1")
    real.symlink_to(tmp_path / "decoy")
    assert placet_common.flag_on(FLAG_NAME) is False                       # a symlink
    real.unlink()
    real.write_text("1")
    os.chmod(real, 0o600)
    os.chmod(hook, 0o770)
    assert placet_common.flag_on(FLAG_NAME) is False                       # directory open to the group
    os.chmod(hook, 0o700)
    assert placet_common.flag_on(FLAG_NAME) is True
    os.chmod(real, 0o666)
    assert placet_common.flag_on(FLAG_NAME) is False                       # file writable by others


def test_reading_the_flag_is_cheap_and_never_raises(hook):
    assert placet_common.flag_on(FLAG_NAME) is False                       # no directory at all
