"""Durable at-most-once dispatch; tests never restart a real service."""
import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from admin_executor.authorization import AuthorizationStore, confirmation
from admin_executor.execution import ExecutionStore, Supervisor
from admin_executor.restart import RestartHandlers


@pytest.fixture
def store(tmp_path):
    path = tmp_path / 'authorization.sqlite'
    AuthorizationStore(path, rp_id='admin.example.test', origin='https://admin.example.test')
    return ExecutionStore(path)


def authorize(store, identifier='one', action='restart-backend', **changes):
    operation = confirmation(action, {}, installed_commit=None, artifact_sha256=None, summary='Restart reviewed service')
    data = json.loads(operation)
    data.update(changes)
    operation = json.dumps(data, sort_keys=True, separators=(',', ':'))
    with sqlite3.connect(store.path) as db:
        db.execute("INSERT INTO authorized_jobs VALUES (?, ?, ?, ?, ?, ?, 'authorized')",
                   (identifier, identifier, operation, hashlib.sha256(operation.encode()).hexdigest(), b'phone', 'SECRET ASSERTION'))


def supervisor(store, handler, enabled=('restart-backend', 'restart-bridge')):
    return Supervisor(store, enabled=frozenset(enabled), handler=handler)


def test_unauthorized_intent_is_not_dispatched(store):
    calls = []
    assert supervisor(store, calls.append).run_once() is None
    assert calls == []


def test_authorized_job_is_executed_once_and_status_survives_restart(store):
    authorize(store)
    calls = []
    worker = supervisor(store, lambda action: calls.append(action) or 'succeeded')
    assert worker.run_once()['state'] == 'succeeded'
    assert worker.run_once() is None
    assert calls == ['restart-backend']
    restored = ExecutionStore(store.path)
    assert restored.status('one')['state'] == 'succeeded'
    assert 'SECRET' not in json.dumps(restored.status('one'))
    assert [e['code'] for e in restored.status('one')['events']] == ['claimed', 'succeeded']


@pytest.mark.parametrize('change', [{'handler_revision': 2}, {'surprise': True}])
def test_changed_handler_or_noncanonical_operation_is_rejected(store, change):
    authorize(store, **change)
    calls = []
    result = supervisor(store, calls.append).run_once()
    assert result['state'] == 'failed'
    assert calls == []


@pytest.mark.parametrize('action,enabled', [('restart-backend', ()), ('unknown', ('unknown',))])
def test_disabled_or_unknown_handler_is_rejected(store, action, enabled):
    authorize(store)
    if action == 'unknown':
        with sqlite3.connect(store.path) as db:
            raw = db.execute('SELECT operation FROM authorized_jobs').fetchone()[0]
            raw = raw.replace('restart-backend', 'unknown')
            db.execute('UPDATE authorized_jobs SET operation=?,digest=?', (raw, hashlib.sha256(raw.encode()).hexdigest()))
    calls = []
    assert supervisor(store, calls.append, enabled).run_once()['state'] == 'failed'
    assert calls == []


def test_digest_mismatch_and_parameters_are_rejected(store):
    authorize(store)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE authorized_jobs SET digest='forged'")
    calls = []
    assert supervisor(store, calls.append).run_once()['state'] == 'failed'
    assert not calls


def test_crash_after_effect_never_repeats_job(store):
    authorize(store)
    calls = []
    def crash(action):
        calls.append(action)
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        supervisor(store, crash).run_once()
    assert store.status('one')['state'] == 'running'
    assert supervisor(store, calls.append).run_once() is None
    assert store.status('one')['state'] == 'outcome_unknown'
    assert calls == ['restart-backend']


def test_concurrent_supervisors_do_not_mark_active_job_as_crashed(store):
    import threading
    authorize(store)
    entered, release = threading.Event(), threading.Event()
    def blocking(action):
        entered.set()
        assert release.wait(5)
        return 'succeeded'
    with ThreadPoolExecutor(2) as pool:
        pending = pool.submit(supervisor(store, blocking).run_once)
        assert entered.wait(5)
        assert supervisor(store, lambda _: pytest.fail('Second dispatch')).run_once() is None
        assert store.status('one')['state'] == 'running'
        release.set()
        assert pending.result()['state'] == 'succeeded'


def test_handler_errors_are_bounded_and_never_expose_secrets(store):
    authorize(store)
    def broken(_):
        raise RuntimeError('SECRET ASSERTION')
    result = supervisor(store, broken).run_once()
    assert result['state'] == 'outcome_unknown'
    assert 'SECRET' not in json.dumps(result)


def test_fixed_backend_restart_checks_new_invocation():
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if 'show' in argv:
            return 'ActiveState=active\nSubState=running\nNRestarts=0\nInvocationID=' + ('old' if len(calls) == 1 else 'new') + '\n'
        return ''
    assert RestartHandlers(health_port=8123, desktop_uid=1000, desktop_gid=1000, run=run,
                           pause=lambda _: None, health=lambda: True)('restart-backend') == 'succeeded'
    assert calls[1][0] == ['/usr/bin/systemctl', 'restart', 'placet-backend.service']
    assert all(c[1]['timeout'] <= 30 for c in calls)
    assert all(c[1]['env'] == {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'} for c in calls)


def test_backend_unchanged_invocation_is_not_success():
    def run(argv, **kwargs):
        return 'ActiveState=active\nSubState=running\nInvocationID=same\n' if 'show' in argv else ''
    handler = RestartHandlers(health_port=8123, desktop_uid=1000, desktop_gid=1000, run=run)
    assert handler('restart-backend') == 'outcome_unknown'


def test_bridge_system_worker_cannot_take_free_arguments():
    from tests.test_admin_bridge_worker import Manager
    manager = Manager()
    handler = RestartHandlers(health_port=8123, desktop_uid=1000, desktop_gid=1000, run=manager)
    assert handler('restart-bridge') == 'succeeded'
    assert all(not {'user','group','extra_groups'} & opts.keys() for _,opts in manager.calls)
    with pytest.raises(ValueError):
        handler('some-shell-command')
    with pytest.raises(ValueError):
        RestartHandlers(health_port=8123, desktop_uid=0, desktop_gid=0)


def test_lock_symlink_is_refused_without_execution(store, tmp_path):
    authorize(store)
    target = tmp_path / 'unrelated'
    target.write_text('unchanged')
    store.path.with_suffix('.execution.lock').symlink_to(target)
    with pytest.raises(OSError):
        supervisor(store, lambda _: pytest.fail('Unexpected execution')).run_once()
    assert target.read_text() == 'unchanged'


def test_backend_timeout_after_request_is_uncertain():
    import subprocess
    def run(argv, **kwargs):
        if 'restart' in argv:
            raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
        return 'ActiveState=active\nSubState=running\nInvocationID=old\n'
    assert RestartHandlers(health_port=8123, desktop_uid=1000, desktop_gid=1000, run=run)('restart-backend') == 'outcome_unknown'


def test_user_manager_missing_does_not_request_restart():
    from admin_executor.restart import BRIDGE, restart_service
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        raise OSError('No user bus')
    assert restart_service(BRIDGE, user=True, run=run) == 'failed'
    assert len(calls) == 1
    assert 'restart' not in calls[0]


def test_supervisor_entrypoint_refuses_nonroot(monkeypatch):
    from admin_executor.supervisor import main
    monkeypatch.setattr('os.geteuid', lambda: 1000)
    with pytest.raises(ValueError, match='root'):
        main()


def test_protected_config_rejects_free_actions_and_root_desktop_identity():
    from admin_executor.supervisor import configuration
    base = {'revision': 1, 'actions': [
        {'id': 'restart-backend', 'enabled': True}, {'id': 'restart-bridge', 'enabled': True},
        {'id': 'deploy', 'enabled': False}]}
    assert configuration(base, {'uid': 1000, 'gid': 1000}) == (frozenset(('restart-backend', 'restart-bridge')), 1000, 1000)
    for catalogue in ({**base, 'revision': 2}, {**base, 'shell': 'cmd'},
                      {**base, 'actions': [{'id': 'deploy', 'enabled': True}]},
                      {**base, 'actions': [{'id': 'restart-backend', 'enabled': 'false'}]}):
        with pytest.raises(ValueError):
            configuration(catalogue, {'uid': 1000, 'gid': 1000})
    with pytest.raises(ValueError):
        configuration(base, {'uid': 0, 'gid': 0})


def test_uncertain_outcome_blocks_later_jobs_until_operator_reconciliation(store):
    authorize(store, 'one')
    authorize(store, 'two')
    calls = []
    worker = supervisor(store, lambda action: calls.append(action) or 'outcome_unknown')
    assert worker.run_once()['state'] == 'outcome_unknown'
    assert worker.run_once() is None
    assert calls == ['restart-backend']
    assert store.status('two') is None


@pytest.mark.parametrize('unstable,healthy', [(True, True), (False, False)])
def test_backend_must_remain_stable_and_healthy_before_success(unstable, healthy):
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        if 'restart' in argv:
            return ''
        number = sum('show' in c for c in calls)
        active = not (unstable and number == 3)
        return ('ActiveState=active\nSubState=running\n' if active else 'ActiveState=failed\nSubState=failed\n') + 'InvocationID=' + ('old' if number == 1 else 'new') + '\nNRestarts=0\n'
    handler = RestartHandlers(health_port=8123, desktop_uid=1000, desktop_gid=1000, run=run,
                              pause=lambda _: None, health=lambda: healthy)
    assert handler('restart-backend') == 'failed'


def test_state_directory_is_private_but_ancestors_may_be_readable(tmp_path):
    import os
    from admin_executor.supervisor import validate_directory
    tmp_path.chmod(0o755)
    validate_directory(tmp_path, private=False, owner=os.geteuid())
    with pytest.raises(ValueError):
        validate_directory(tmp_path, private=True, owner=os.geteuid())
    tmp_path.chmod(0o700)
    validate_directory(tmp_path, private=True, owner=os.geteuid())
    tmp_path.chmod(0o777)
    with pytest.raises(ValueError):
        validate_directory(tmp_path, private=False, owner=os.geteuid())


def test_backend_automatic_restart_during_stabilization_is_failure():
    reads = 0
    def run(argv, **kwargs):
        nonlocal reads
        if 'restart' in argv:
            return ''
        reads += 1
        return ('ActiveState=active\nSubState=running\nInvocationID=' + ('old' if reads == 1 else 'new')
                + '\nNRestarts=' + ('1' if reads == 3 else '0') + '\n')
    handler = RestartHandlers(health_port=8123, desktop_uid=1000, desktop_gid=1000, run=run,
                              pause=lambda _: None, health=lambda: True)
    assert handler('restart-backend') == 'failed'


def test_health_port_is_validated_protected_configuration():
    from admin_executor.supervisor import health_configuration
    assert health_configuration({'port': 8123}) == 8123
    for value in ({'port': 0}, {'port': 65536}, {'port': True}, {'port': '8000'},
                  {'port': 8123, 'host': 'external.example'}, {}):
        with pytest.raises(ValueError):
            health_configuration(value)


def test_backend_health_uses_only_configured_loopback_port(monkeypatch):
    from admin_executor.restart import backend_health
    calls = []
    class Connection:
        def __init__(self, host, port, timeout):
            calls.append((host, port, timeout))
        def request(self, method, path):
            calls.append((method, path))
        def getresponse(self):
            return type('Response', (), {'status': 200})()
        def close(self):
            pass
    monkeypatch.setattr('admin_executor.restart.HTTPConnection', Connection)
    assert backend_health(8123) is True
    assert calls == [('127.0.0.1', 8123, 2), ('GET', '/api/health')]
