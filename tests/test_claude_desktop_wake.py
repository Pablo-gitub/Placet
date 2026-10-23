import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from bridge.claude_desktop import ClaudeDesktopWake
from bridge.bridge_daemon import BridgeDaemon
from tests.test_session_messages import setup, start, write
from tests.helpers import agent_client

SID = 'a2c3ef2a-6d99-4567-9909-9fc96489ebf7'
LOCAL = 'local_bbe53ab6-febe-44e7-8fc4-c77a64636737'


def metadata(root, project_dir, **overrides):
    path = root / 'account' / 'org' / (LOCAL + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {'sessionId': LOCAL, 'cliSessionId': SID, 'cwd': str(project_dir), 'isArchived': False}
    path.write_text(json.dumps({**data, **overrides}))
    return path


def test_native_link_opens_same_session_once_even_after_bridge_restart(tmp_path):
    root, state = tmp_path / 'sessions', tmp_path / 'state'
    metadata(root, tmp_path)
    run = MagicMock(return_value=SimpleNamespace(returncode=0))
    opener = ClaudeDesktopWake(root, state, run, clock=lambda: 1000)
    assert opener.open(SID, str(tmp_path))
    assert not opener.open(SID, str(tmp_path))
    assert not ClaudeDesktopWake(root, state, run, clock=lambda: 1100).open(SID, str(tmp_path))
    run.assert_called_once_with(['/usr/bin/claude-desktop', 'claude://code/continue?session=' + LOCAL],
                                capture_output=True, text=True, timeout=20)
    assert (state.stat().st_mode & 0o777) == 0o700
    assert ((state / (SID + '.attempt')).stat().st_mode & 0o777) == 0o600
    assert ClaudeDesktopWake(root, state, run, clock=lambda: 1301).open(SID, str(tmp_path))


@pytest.mark.parametrize('overrides', [{'isArchived': True}, {'cwd': '/wrong'}, {'sessionId': 'bad'}])
def test_wrong_mapping_never_opens(tmp_path, overrides):
    root = tmp_path / 'sessions'
    metadata(root, tmp_path, **overrides)
    run = MagicMock()
    with pytest.raises(ValueError):
        ClaudeDesktopWake(root, tmp_path / 'state', run).open(SID, str(tmp_path))
    run.assert_not_called()


def test_missing_or_duplicate_mapping_does_not_choose_another_chat(tmp_path):
    root = tmp_path / 'sessions'
    opener = ClaudeDesktopWake(root, tmp_path / 'state')
    with pytest.raises(ValueError):
        opener.resolve(SID, str(tmp_path))
    p = metadata(root, tmp_path)
    other = root / 'another' / 'org' / p.name
    other.parent.mkdir(parents=True)
    other.write_text(p.read_text())
    with pytest.raises(ValueError):
        opener.resolve(SID, str(tmp_path))


def test_failed_open_is_throttled_and_off_project_not_opened(tmp_path):
    root = tmp_path / 'sessions'
    metadata(root, tmp_path)
    run = MagicMock(return_value=SimpleNamespace(returncode=1))
    opener = ClaudeDesktopWake(root, tmp_path / 'state', run)
    with pytest.raises(RuntimeError):
        opener.open(SID, str(tmp_path))
    assert not opener.open(SID, str(tmp_path))
    (tmp_path / '.placet-off').touch()
    assert not opener.open(SID, str(tmp_path))
    run.assert_called_once()


def test_daemon_wakes_without_claiming_or_confirming_mail():
    store, waker = MagicMock(), MagicMock()
    daemon = BridgeDaemon(store=store, tmux=MagicMock(), channels={}, claude_waker=waker)
    store.reset_mock()
    target = [{'id': SID, 'cwd': '/lab'}]
    daemon.wake_claude_sessions(target)
    daemon.wake_claude_sessions(target)
    waker.open.assert_called_once_with(SID, '/lab')
    assert not store.mock_calls


@pytest.mark.asyncio
async def test_only_unreachable_idle_desktop_with_targeted_mail_is_listed(setup):
    app, db = setup
    await start(app, 'claude', SID, 'claude-code')
    db.upsert_session(SID, 'claude', entrypoint='claude-desktop', cwd='/lab')
    other = 'e24d69ad-faf5-43c9-94c4-a8e8a6e886a0'
    db.upsert_session(other, 'claude', provider='claude-code', entrypoint='claude-desktop', cwd='/other')
    req_id = (await write(app, SID, 'one pending message')).json()['id']
    async with agent_client(app, 'tok-bridge') as bridge:
        data = (await bridge.get('/api/bridge/work')).json()
        assert data['claude_wake_sessions'] == [{'id': SID, 'cwd': '/lab'}]
        assert db.get_request(req_id).delivery == 'waiting'
        db.touch_waiter(SID)
        assert (await bridge.get('/api/bridge/work')).json()['claude_wake_sessions'] == []
    # No claim or synthetic execution event has occurred.
    assert db.get_request(req_id).execution == 'unobserved'

@pytest.mark.asyncio
@pytest.mark.parametrize('condition', ['busy', 'ended', 'cli', 'excluded', 'generic', 'in_flight'])
async def test_unavailable_or_untargeted_sessions_do_not_trigger_open(setup, condition):
    from backend.app.models import RequestCreate, RequestStatus
    app, db = setup
    await start(app, 'claude', SID, 'claude-code')
    db.upsert_session(SID, 'claude', entrypoint='claude-desktop', cwd='/lab')
    req_id = (await write(app, SID, 'one message')).json()['id']
    if condition == 'busy':
        db.set_session_state(SID, 'busy')
    elif condition == 'ended':
        db.end_session(SID)
    elif condition == 'cli':
        db.upsert_session(SID, 'claude', entrypoint='cli')
    elif condition == 'excluded':
        db.set_excluded_folders(['/lab'], ['/lab'])
    elif condition == 'generic':
        db.set_recipient_session(req_id, None)
    else:
        db.claim_injection(req_id)
        db.confirm_injection(req_id, actor='bridge', via='test')
        await write(app, SID, 'second message')
    assert db.claude_sessions_to_wake() == []
    if condition == 'generic':
        # Not a reason to open the app, but still for the session's waiter.
        assert db.inbox_has_message(db.get_session(SID))


def test_a_session_that_moved_into_a_worktree_of_its_folder_still_resolves(tmp_path):
    """The app records the folder the chat was opened in; the hooks report where the agent cd'ed to."""
    root = tmp_path / 'sessions'
    metadata(root, tmp_path)
    inner = tmp_path / '.worktrees' / 'feature' / 'frontend'
    inner.mkdir(parents=True)
    assert ClaudeDesktopWake(root, tmp_path / 'state').resolve(SID, str(inner)) == LOCAL


def test_a_folder_outside_the_recorded_one_still_never_opens(tmp_path):
    root = tmp_path / 'sessions'
    project = tmp_path / 'project'
    project.mkdir()
    metadata(root, project)
    sibling = tmp_path / 'project-evil'
    sibling.mkdir()
    with pytest.raises(ValueError):
        ClaudeDesktopWake(root, tmp_path / 'state').resolve(SID, str(sibling))
    with pytest.raises(ValueError):
        ClaudeDesktopWake(root, tmp_path / 'state').resolve(SID, str(project / '..' / 'project-evil'))
