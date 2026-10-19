"""Offline activation guards; no test changes a host service or account."""
import json
import os
import sqlite3
from pathlib import Path

import pytest

from admin_executor.execution import ExecutionStore, Supervisor
from tests.test_admin_execution import authorize, store


def test_reconciliation_clears_uncertainty_without_repeating_the_effect(store):
    identifier = 'a' * 32
    authorize(store, identifier)
    calls = []
    worker = Supervisor(store, enabled={'restart-backend'}, handler=lambda a: calls.append(a) or 'outcome_unknown')
    worker.run_once()
    result = store.reconcile(identifier, 'succeeded')
    assert result['state'] == 'succeeded'
    assert result['events'][-1]['code'] == 'operator_reconciled_succeeded'
    assert store.reconcile(identifier, 'succeeded') == result
    with pytest.raises(ValueError):
        store.reconcile(identifier, 'failed')
    assert worker.run_once() is None
    assert calls == ['restart-backend']


def test_reconciliation_cannot_race_live_supervisor_or_follow_lock_link(store, tmp_path):
    identifier = 'b' * 32
    authorize(store, identifier)
    def handler(_):
        with pytest.raises(ValueError, match='active'):
            store.reconcile(identifier, 'failed')
        return 'outcome_unknown'
    Supervisor(store, enabled={'restart-backend'}, handler=handler).run_once()
    lock = store.path.with_suffix('.execution.lock')
    lock.unlink()
    target = tmp_path / 'target'
    target.write_text('untouched')
    lock.symlink_to(target)
    with pytest.raises(OSError):
        store.reconcile(identifier, 'failed')
    assert target.read_text() == 'untouched'


def test_reconciliation_refuses_unclaimed_jobs_and_free_outcomes(store):
    identifier = 'c' * 32
    authorize(store, identifier)
    for job, outcome in [(identifier, 'failed'), ('invalid', 'failed'), (identifier, 'running')]:
        with pytest.raises(ValueError):
            store.reconcile(job, outcome)


def test_preflight_blocks_retained_process_groups_and_socket_access():
    from admin_executor.activation_checks import assess
    safe = dict(desktop_groups={'users'}, process_groups={'users'}, peer_group_members=set(),
                privileged_sockets=[], unassessed_groups=set())
    assert assess(**safe) == []
    for change in [dict(desktop_groups={'docker'}), dict(process_groups={'docker'}),
                   dict(privileged_sockets=['/var/run/docker.sock']),
                   dict(peer_group_members={'paolo'}), dict(unassessed_groups={'libvirt'})]:
        assert assess(**(safe | change))
    assert assess(**(safe | {'desktop_groups': {'kvm'}})) == []


def test_units_have_bounded_restarts_and_only_required_capabilities():
    from admin_executor.activation_units import units
    result = units()
    assert set(result) == {'placet-admin-control.service', 'placet-admin-supervisor.service'}
    for text in result.values():
        for setting in ['RestartSec=5', 'StartLimitBurst=3', 'StartLimitIntervalSec=60',
                        'NoNewPrivileges=yes', 'KillMode=control-group', 'ProtectSystem=strict']:
            assert setting in text
        assert 'CAP_SYS_ADMIN' not in text and 'CAP_DAC_OVERRIDE' not in text
    assert 'CapabilityBoundingSet=CAP_SETUID CAP_SETGID' in result['placet-admin-supervisor.service']
    assert 'CapabilityBoundingSet=CAP_CHOWN' in result['placet-admin-control.service']
    assert 'RuntimeDirectoryMode=0711' in result['placet-admin-control.service']
    # ProtectHome=yes also hides /run/user and would break the dropped helper.
    assert 'ProtectHome=read-only' in result['placet-admin-supervisor.service']
    assert 'InaccessiblePaths=-/home -/root' in result['placet-admin-supervisor.service']


def test_activation_preview_has_no_subprocess_or_filesystem_side_effects(monkeypatch, capsys):
    from admin_executor import activation_bootstrap
    monkeypatch.setattr(activation_bootstrap.subprocess, 'run', lambda *a, **kw: pytest.fail('Preview executed a command'))
    activation_bootstrap.main(['--desktop-user', 'paolo', '--return-url', 'https://placet.example.test/', '--health-port', '8000'])
    text = capsys.readouterr().out
    assert 'preview' in text and 'bundle_sha256' in text
    assert 'installed_sha256' in text


def test_activation_apply_requires_root_and_both_pins_before_any_mutation(monkeypatch):
    from admin_executor import activation_bootstrap
    monkeypatch.setattr(activation_bootstrap.os, 'geteuid', lambda: 1000)
    monkeypatch.setattr(activation_bootstrap.subprocess, 'run', lambda *a, **kw: pytest.fail('Unpinned apply executed a command'))
    with pytest.raises(SystemExit):
        activation_bootstrap.main(['--desktop-user', 'paolo', '--return-url', 'https://placet.example.test/',
                                   '--health-port', '8000', '--apply'])


def test_cli_contract_documented_and_reconciliation_does_not_require_web_mailbox(monkeypatch, capsys):
    from admin_executor import terminal
    source = Path('frontend/src/Commands.jsx').read_text()
    prefix = source.split('const TERMINAL = "', 1)[1].split('";', 1)[0]
    docs = Path('docs/installation.md').read_text()
    for outcome in ('succeeded', 'failed'):
        assert prefix + ' reconcile --id <job_id> --outcome ' + outcome in docs
    monkeypatch.setattr(terminal.os, 'geteuid', lambda: 0)
    seen = []
    monkeypatch.setattr(terminal, 'reconcile_job', lambda identifier, outcome: seen.append((identifier, outcome)))
    terminal.main(['reconcile', '--id', 'd' * 32, '--outcome', 'failed'])
    assert seen == [('d' * 32, 'failed')]
    assert 'reconciled' in capsys.readouterr().out


def test_activation_manifest_rejects_intermediate_links_and_unpinned_changes(tmp_path):
    from admin_executor.activation_bootstrap import snapshot
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'bootstrap.py').write_text('MODULES = ("bootstrap.py", "assets/a.txt")')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'a.txt').write_text('do not read')
    (source / 'assets').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='links'):
        snapshot(source)
    (source / 'assets').unlink()
    (source / 'assets').mkdir()
    (source / 'assets/a.txt').write_text('reviewed')
    first = snapshot(source)[1]
    (source / 'assets/a.txt').write_text('changed')
    assert snapshot(source)[1] != first


@pytest.mark.skipif(os.environ.get('PLACET_TEST_SYSTEMD') != '1', reason='Explicit host parser check')
def test_activation_units_parse_on_the_host(tmp_path):
    import subprocess
    from admin_executor.activation_units import units
    for name, text in units().items():
        path = tmp_path / name
        path.write_text(text)
        result = subprocess.run(['/usr/bin/systemd-analyze', 'verify', '--man=no', str(path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr


@pytest.fixture
def upgrade(tmp_path, monkeypatch):
    """Fake protected installation; all service/process operations are intercepted."""
    import types
    from admin_executor import activation_bootstrap as installer
    from admin_executor.bootstrap import snapshot
    from admin_executor.authorization import AuthorizationStore
    from admin_executor.web_service import UNIT
    root = tmp_path / 'root'
    root.mkdir()
    prefix, config, state, units, venv = [root / p for p in ('code', 'config', 'state', 'units', 'venv')]
    package = prefix / 'admin_executor'
    for path in (package, config, state, units, venv):
        path.mkdir(parents=True)
    files, digest = snapshot(Path('admin_executor'))
    for name, data in files.items():
        path = package / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (config / 'origin').write_text('https://admin.example.test')
    (config / 'catalogue.json').write_text(json.dumps({'revision': 1, 'actions': [
        {'id': a, 'enabled': False} for a in ('deploy', 'restart-backend', 'restart-bridge')]}))
    (units / 'placet-admin-web.service').write_text(UNIT)
    database = state / 'authorization.sqlite'
    AuthorizationStore(database, rp_id='admin.example.test', origin='https://admin.example.test')
    with sqlite3.connect(database) as db:
        db.execute('INSERT INTO credentials VALUES (?,?,?,?)', (b'phone', b'unchanged key', 3, 0))
    for key, value in dict(PREFIX=prefix, PACKAGE=package, CONFIG=config, STATE=state, UNITS=units, VENV=venv).items():
        monkeypatch.setattr(installer, key, value)
    monkeypatch.setattr(installer, 'protected', lambda *a, **kw: None)
    checks = types.SimpleNamespace(inspect_host=lambda _: tuple(types.SimpleNamespace(
        pw_uid=i, pw_gid=i, pw_name=n) for i, n in [(1000, 'desktop'), (994, 'web'), (995, 'placet')]))
    original_frozen = installer.frozen
    monkeypatch.setattr(installer, 'frozen', lambda bundle, name: checks if name == 'activation_checks' else original_frozen(bundle, name))
    calls = []
    monkeypatch.setattr(installer, 'run', lambda *a: calls.append(a) or types.SimpleNamespace(stdout='placet\n'))
    monkeypatch.setattr(installer.os, 'getgrouplist', lambda *a: [1000])
    monkeypatch.setattr(installer.subprocess, 'run', lambda *a, **kw: types.SimpleNamespace(returncode=1))
    args = types.SimpleNamespace(installed_sha256=digest, desktop_user='desktop', health_port=8123,
                                 return_url='https://placet.example.test/')
    return installer, files, args, calls, checks


def test_upgrade_preserves_credentials_and_enables_only_after_services_start(upgrade):
    installer, files, args, calls, _ = upgrade
    installer.apply(files, args)
    with sqlite3.connect(installer.STATE / 'authorization.sqlite') as db:
        assert db.execute('SELECT * FROM credentials').fetchone() == (b'phone', b'unchanged key', 3, 0)
        assert db.execute('SELECT COUNT(*) FROM authorized_jobs').fetchone()[0] == 0
    with sqlite3.connect(installer.STATE / 'activation-before.sqlite') as db:
        assert db.execute('SELECT public_key FROM credentials').fetchone()[0] == b'unchanged key'
    assert (installer.PREFIX / ('admin_executor.before-activation-' + args.installed_sha256)).is_dir()
    assert installer.PACKAGE.stat().st_mode & 0o777 == 0o755
    assert (installer.PACKAGE / 'web_static').stat().st_mode & 0o777 == 0o755
    assert (installer.STATE / 'authorization.sqlite').stat().st_mode & 0o777 == 0o600
    assert json.loads((installer.CONFIG / 'health.json').read_text()) == {'port': 8123}
    catalogue = json.loads((installer.CONFIG / 'catalogue.json').read_text())
    assert {a['id'] for a in catalogue['actions'] if a['enabled']} == {'restart-backend', 'restart-bridge'}
    assert not any('tailscale' in ' '.join(call) for call in calls)
    with pytest.raises(ValueError, match='disabled'):
        installer.apply(files, args)


def test_failed_preflight_performs_no_service_or_code_changes(upgrade):
    installer, files, args, calls, checks = upgrade
    before = installer.snapshot(installer.PACKAGE)[1]
    def fail(_):
        raise ValueError('Docker access')
    checks.inspect_host = fail
    with pytest.raises(ValueError, match='Docker'):
        installer.apply(files, args)
    assert not calls
    assert installer.snapshot(installer.PACKAGE)[1] == before
    assert not (installer.STATE / 'activation-before.sqlite').exists()


def test_failed_start_leaves_catalogue_disabled_and_preserves_backup(upgrade, monkeypatch):
    installer, files, args, _, _ = upgrade
    def fail_start(*a):
        if 'enable' in a:
            raise RuntimeError('simulated start failure')
        return type('Result', (), {'stdout': 'placet\n'})()
    monkeypatch.setattr(installer, 'run', fail_start)
    with pytest.raises(RuntimeError):
        installer.apply(files, args)
    assert not any(a['enabled'] for a in json.loads((installer.CONFIG / 'catalogue.json').read_text())['actions'])
    assert (installer.STATE / 'activation-before.sqlite').is_file()


def test_host_inspection_catches_groups_retained_after_nss_removal(tmp_path, monkeypatch):
    import types
    from admin_executor import activation_checks as checks
    accounts = [types.SimpleNamespace(pw_uid=i, pw_gid=i, pw_name=name, pw_shell='/usr/sbin/nologin')
                for i, name in [(1000, 'desktop'), (994, 'placet-admin-web'), (995, 'placet')]]
    monkeypatch.setattr(checks.pwd, 'getpwnam', lambda n: next(a for a in accounts if a.pw_name == n))
    monkeypatch.setattr(checks.pwd, 'getpwall', lambda: accounts)
    monkeypatch.setattr(checks.os, 'getgrouplist', lambda n, g: [g])
    def group(gid):
        return types.SimpleNamespace(gr_name='docker' if gid == 999 else 'user' + str(gid), gr_mem=[])
    monkeypatch.setattr(checks.grp, 'getgrgid', group)
    proc = tmp_path / 'proc'
    (proc / '12').mkdir(parents=True)
    (proc / '12/status').write_text('Uid:\t1000 1000 1000 1000\nGid:\t1000 1000 1000 1000\nGroups:\t1000 999\n')
    class FakePath:
        def __new__(cls, path):
            # No live sockets or /proc are inspected by this test.
            return proc if path == '/proc' else tmp_path / ('missing-' + path.replace('/', '_'))
    monkeypatch.setattr(checks, 'Path', FakePath)
    with pytest.raises(ValueError, match='root-equivalent'):
        checks.inspect_host('desktop')
    (proc / '12/status').write_text('Uid:\t1000 1000 1000 1000\nGid:\t1000 1000 1000 1000\nGroups:\t1000\n')
    assert checks.inspect_host('desktop')[0].pw_uid == 1000
    monkeypatch.setattr(checks.os, 'getgrouplist', lambda n, g: [g, 999] if n == 'placet' else [g])
    with pytest.raises(ValueError, match='extra groups'):
        checks.inspect_host('desktop')


def test_unsafe_installed_code_is_rejected_before_any_service_call(upgrade, monkeypatch):
    installer, files, args, calls, _ = upgrade
    def reject(path, **kwargs):
        if path == installer.PACKAGE:
            raise ValueError('Unprotected installed code')
    monkeypatch.setattr(installer, 'protected', reject)
    with pytest.raises(ValueError, match='Unprotected'):
        installer.apply(files, args)
    assert calls == []


def test_passkey_origin_and_unattended_sudo_are_activation_blockers(upgrade, monkeypatch):
    installer, files, args, calls, _ = upgrade
    original = installer.run
    monkeypatch.setattr(installer, 'run', lambda *a: type('Result', (), {'stdout': 'NOPASSWD: ALL'})()
                        if a[0] == '/usr/bin/sudo' else original(*a))
    with pytest.raises(ValueError, match='sudo policy'):
        installer.apply(files, args)
    assert not (installer.STATE / 'activation-before.sqlite').exists()
    monkeypatch.setattr(installer, 'run', original)
    with sqlite3.connect(installer.STATE / 'authorization.sqlite') as db:
        db.execute("UPDATE identity SET origin='https://wrong.example.test'")
    with pytest.raises(ValueError, match='passkey origin'):
        installer.apply(files, args)
    assert not (installer.STATE / 'activation-before.sqlite').exists()


def test_confirmation_page_explains_manual_return_in_both_languages():
    markup = Path('admin_executor/web_static/index.html').read_text()
    assert markup.index('data-i18n="returnToApp"') < markup.index('id="return-link"')
    for language in ('en', 'it'):
        catalogue = json.loads(Path(f'admin_executor/web_static/{language}.json').read_text())
        assert catalogue['returnToApp'] and catalogue['returnToPlacet']


def test_enrollment_cli_rejects_reconciliation_without_a_complete_outcome(monkeypatch):
    from admin_executor import terminal
    monkeypatch.setattr(terminal.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(terminal, 'reconcile_job', lambda *a: pytest.fail('Invalid command reached state'))
    for argv in (['reconcile', '--id', 'e' * 32], ['reconcile', '--id', 'short', '--outcome', 'failed'],
                 ['begin-enrollment', '--outcome', 'succeeded']):
        with pytest.raises(SystemExit):
            terminal.main(argv)
