import io
import json
from pathlib import Path

from hooks import placet_waiter as waiter
from hooks.install import claude_hooks


def test_waiter_timeout_is_explicit_and_longer_than_a_day():
    for event in ('SessionStart', 'Stop'):
        hook = next(h for h in claude_hooks('/opt/placet/hooks')[event][0]['hooks'] if h.get('asyncRewake'))
        assert hook['timeout'] == 7 * 86400
        assert hook['timeout'] * 1000 < 2**31


def test_lock_is_atomic_and_survives_stale_pid(tmp_path, monkeypatch):
    monkeypatch.setattr(waiter, 'LOCK_DIR', tmp_path)
    path = tmp_path / 's.pid'
    path.write_text('1')  # PID reuse must not disable a new listener.
    first = waiter._acquire('s')
    assert first is not None
    assert waiter._acquire('s') is None
    first.close()
    second = waiter._acquire('s')
    assert second is not None
    second.close()


def test_waiter_survives_long_idle_then_delivers_once(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(waiter, 'LOCK_DIR', tmp_path)
    monkeypatch.setattr(waiter.sys, 'stdin', io.StringIO(json.dumps({'session_id': 's', 'cwd': str(tmp_path)})))
    monkeypatch.setattr(waiter.placet_common, 'agent_id', lambda _: 'claude')
    monkeypatch.setattr(waiter.placet_common, 'token_for', lambda _: 'token')
    monkeypatch.setattr(waiter, '_owner', lambda: (123, 'start'))
    monkeypatch.setattr(waiter, '_owner_alive', lambda _: True)
    # 400 long polls represent over a day of inactivity. No clock deadline
    # inside the waiter may terminate it before the queued message arrives.
    responses = iter([(204, None)] * 400 + [(200, {'text': 'only once'})])
    monkeypatch.setattr(waiter.placet_common, 'call', lambda *a, **k: next(responses))
    assert waiter.main() == 2
    assert capsys.readouterr().err == 'only once\n'
    assert (tmp_path / 's.pid').read_text() == ''


def test_engine_pid_reuse_and_zombie_do_not_keep_waiter_alive(monkeypatch):
    from types import SimpleNamespace
    fields = ['S'] + ['0'] * 18 + ['42']
    monkeypatch.setattr(waiter, 'Path', lambda _: SimpleNamespace(read_text=lambda: '123 (claude) ' + ' '.join(fields)))
    assert waiter._owner_alive((123, '42'))
    assert not waiter._owner_alive((123, '41'))
    fields[0] = 'Z'
    assert not waiter._owner_alive((123, '42'))
