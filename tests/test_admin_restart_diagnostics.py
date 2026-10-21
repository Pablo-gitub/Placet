"""Closed restart diagnostics, without invoking any service manager."""
import json
import sqlite3
import subprocess
import sys

import pytest

from admin_executor.restart import RestartHandlers, restart_service, BRIDGE, run_fixed, SpawnFailure, decode_report
from admin_executor.execution import Supervisor
from tests.test_admin_execution import store, authorize


def handler(run):
    return RestartHandlers(desktop_uid=1000, desktop_gid=1000, health_port=8000, run=run)


@pytest.mark.parametrize('error,state,code', [
    (SpawnFailure(1), 'failed', 'worker_manager_unreachable'),
    (OSError(5, 'SECRET read failure'), 'failed', 'worker_manager_unreachable'),
    (subprocess.CalledProcessError(2, ['fixed'], stderr=b'SECRET'), 'failed', 'worker_manager_unreachable'),
    (subprocess.TimeoutExpired(['fixed'], 40, stderr=b'SECRET'), 'failed', 'worker_manager_unreachable'),
])
def test_worker_preflight_failures_preserve_safe_phase(error, state, code):
    def run(*a, **kw):
        raise error
    result = handler(run)('restart-bridge')
    assert str(result) == state
    assert result.code == code
    assert 'SECRET' not in result.code


def test_helper_protocol_is_closed_and_rejects_raw_output():
    for output in ('succeeded\n', 'SECRET', '{"state":"failed","code":"SECRET"}',
                   '{"state":"succeeded","code":"helper_spawn_failed"}',
                   '{"state":"failed","code":"manager_unreachable","stderr":"SECRET"}'):
        with pytest.raises(ValueError):
            decode_report(output)
    result = decode_report(json.dumps({'state':'failed','code':'manager_unreachable'}))
    assert str(result) == 'failed' and result.code == 'manager_unreachable'


def status(invocation='old', active=True):
    return ('ActiveState=active\nSubState=running\n' if active else 'ActiveState=failed\nSubState=failed\n') + f'InvocationID={invocation}\nNRestarts=0\n'


@pytest.mark.parametrize('scenario,state,code', [
    ('bus_missing', 'failed', 'manager_unreachable'),
    ('rejected', 'outcome_unknown', 'restart_rejected'),
    ('timeout', 'outcome_unknown', 'restart_timeout'),
    ('not_sent', 'failed', 'restart_not_sent'),
    ('unchanged', 'outcome_unknown', 'invocation_unchanged'),
    ('crash', 'outcome_unknown', 'verification_failed'),
    ('lost_response', 'outcome_unknown', 'verification_failed'),
    ('success', 'succeeded', 'restart_confirmed'),
])
def test_user_manager_effect_and_phase_are_distinct(scenario, state, code):
    calls = []
    reads = 0
    def run(argv, **kw):
        nonlocal reads
        calls.append(argv)
        if 'restart' in argv:
            if scenario == 'rejected':
                raise subprocess.CalledProcessError(1, argv, stderr=b'SECRET')
            if scenario == 'timeout':
                raise subprocess.TimeoutExpired(argv, 15)
            if scenario == 'not_sent':
                raise SpawnFailure(13)
            return ''
        reads += 1
        if scenario == 'bus_missing' or (scenario == 'lost_response' and reads > 1):
            raise OSError('SECRET')
        return status('old' if reads == 1 or scenario == 'unchanged' else 'new',
                      not (scenario == 'crash' and reads >= 3))
    result = restart_service(BRIDGE, user=True, run=run, pause=lambda _: None)
    assert str(result) == state and result.code == code
    assert sum('restart' in call for call in calls) <= 1
    if scenario == 'bus_missing':
        assert not any('restart' in call for call in calls)


def test_durable_job_stores_only_phase_code_and_never_retries_uncertainty(store):
    authorize(store, 'd' * 32, action='restart-bridge')
    calls = []
    from tests.test_admin_bridge_worker import state
    def run(argv, **kw):
        calls.append(argv)
        if 'show' in argv:
            return state()
        raise subprocess.CalledProcessError(2, ['fixed'], stderr=b'SECRET ASSERTION')
    worker = Supervisor(store, enabled={'restart-bridge'}, handler=handler(run))
    result = worker.run_once()
    assert result['state'] == 'outcome_unknown'
    assert result['result_code'] == 'worker_dispatch_uncertain'
    assert result['events'][-1]['code'] == 'worker_dispatch_uncertain'
    assert worker.run_once() is None and len(calls) == 2
    assert 'SECRET' not in json.dumps(result)


def test_fixed_runner_bounds_output_and_does_not_preserve_stderr():
    with pytest.raises(ValueError):
        run_fixed([sys.executable, '-I', '-c', 'print("x" * 100000)'], timeout=5)
    with pytest.raises(subprocess.CalledProcessError) as error:
        run_fixed([sys.executable, '-I', '-c', 'import sys;sys.stderr.write("SECRET");sys.exit(2)'], timeout=5)
    assert error.value.stderr is None and error.value.output is None


def test_helper_import_failure_emits_only_a_closed_report(monkeypatch, capsys):
    import builtins
    from admin_executor import bridge_restart
    original = builtins.__import__
    def fail(name, *a, **kw):
        if name == 'restart':
            raise ImportError('SECRET IMPORT ERROR')
        return original(name, *a, **kw)
    monkeypatch.setattr(bridge_restart.os, 'geteuid', lambda: 1000)
    monkeypatch.setattr(bridge_restart, 'read_policy', lambda: {})
    monkeypatch.setattr(bridge_restart, 'verify_identity', lambda *a: None)
    monkeypatch.setattr(bridge_restart, 'notify', print)
    monkeypatch.setattr(sys, 'argv', ['bridge_restart.py'])
    monkeypatch.setattr(sys, 'path', sys.path[:])
    monkeypatch.setattr(builtins, '__import__', fail)
    bridge_restart.main()
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {'state': 'failed', 'code': 'helper_import_failed'}
    assert not captured.err and 'SECRET' not in captured.out


def test_fixed_runner_timeout_has_no_partial_output():
    with pytest.raises(subprocess.TimeoutExpired) as error:
        run_fixed([sys.executable, '-I', '-c', 'import time;print("SECRET",flush=True);time.sleep(5)'], timeout=0.1)
    assert error.value.output is None and error.value.stderr is None


def test_codes_are_present_in_both_public_catalogues():
    from pathlib import Path
    from admin_executor.restart import RESULT_CODES
    root = Path(__file__).resolve().parent.parent
    for language in ('en', 'it'):
        pwa = json.loads((root / f'frontend/src/i18n/locales/{language}.json').read_text())
        admin = json.loads((root / f'admin_executor/web_static/{language}.json').read_text())
        for code in RESULT_CODES:
            assert pwa['commands']['results'][code] == admin['result_' + code]


def test_invalid_initial_manager_response_never_dispatches_a_restart():
    calls = []
    def run(argv, **kw):
        calls.append(argv)
        return 'SECRET malformed output'
    result = restart_service(BRIDGE, user=True, run=run, pause=lambda _: None)
    assert str(result) == 'failed' and result.code == 'manager_response_invalid'
    assert len(calls) == 1 and 'restart' not in calls[0]


def test_empty_new_invocation_cannot_succeed():
    reads = 0
    def run(argv, **kw):
        nonlocal reads
        if 'restart' in argv:
            return ''
        reads += 1
        return status('old' if reads == 1 else '')
    result = restart_service(BRIDGE, user=True, run=run, pause=lambda _: None)
    assert str(result) == 'outcome_unknown' and result.code == 'verification_failed'


def test_popen_failure_is_typed_and_has_no_exception_text(monkeypatch):
    import admin_executor.restart as restart
    def denied(*a, **kw):
        raise PermissionError(1, 'SECRET preexec')
    monkeypatch.setattr(restart.subprocess, 'Popen', denied)
    outcome = handler(restart.run_fixed)('restart-bridge')
    assert str(outcome) == 'failed' and outcome.code == 'worker_manager_unreachable'


def test_io_error_after_dispatch_is_never_a_certain_failure():
    def run(argv, **kw):
        if 'restart' in argv:
            raise OSError(5, 'SECRET pipe error after sending')
        return status()
    outcome = restart_service(BRIDGE, user=True, run=run, pause=lambda _: None)
    assert str(outcome) == 'outcome_unknown' and outcome.code == 'restart_unconfirmed'
