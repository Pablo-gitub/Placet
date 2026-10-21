"""Worker maintenance is planned and simulated offline; never use root."""
import json
from pathlib import Path
import sqlite3

import pytest


def test_maintenance_preview_does_not_run_commands(monkeypatch,capsys):
    from admin_executor import worker_maintenance as m
    monkeypatch.setattr(m.subprocess,'run',lambda *a,**k: pytest.fail('Preview executed command'))
    m.main([])
    out=json.loads(capsys.readouterr().out)
    assert out['mode']=='preview' and out['services_restarted']==[]
    assert len(out['bundle_sha256'])==64


def test_maintenance_apply_needs_root_protected_copy_and_pins(monkeypatch):
    from admin_executor import worker_maintenance as m
    monkeypatch.setattr(m.os,'geteuid',lambda:1000)
    monkeypatch.setattr(m.subprocess,'run',lambda *a,**k:pytest.fail('Apply ran a command'))
    with pytest.raises(SystemExit): m.main(['--apply'])


def test_unit_renderer_reads_only_literal_template_not_checkout_code():
    from admin_executor import worker_maintenance as m
    from admin_executor.worker_units import worker_unit
    source=Path('admin_executor/worker_units.py').read_bytes()
    assert m.render_unit(source,1000,1000)==worker_unit(uid=1000,gid=1000)
    with pytest.raises(ValueError):
        m.render_unit(b'def worker_unit():\n return dangerous()\n',1000,1000)


def test_maintenance_limits_changed_modules_and_never_updates_other_units():
    from admin_executor import worker_maintenance as m
    old={'bootstrap.py':b'old','restart.py':b'old','authorization.py':b'fixed'}
    new={**old,'restart.py':b'new','bridge_worker.py':b'new'}
    m.validate_changes(old,new)
    for modified in ({**new,'authorization.py':b'changed'}, {'restart.py':b'new'}):
        with pytest.raises(ValueError):m.validate_changes(old,modified)
    assert m.WORKER=='placet-admin-bridge-worker.service'


def test_pending_or_uncertain_jobs_block_maintenance_without_modification(tmp_path):
    from admin_executor import worker_maintenance as m
    dbpath=tmp_path/'authorization.sqlite'
    with sqlite3.connect(dbpath) as db:
        db.executescript('''CREATE TABLE authorized_jobs(id,state); CREATE TABLE execution_jobs(job_id,state);
          INSERT INTO authorized_jobs VALUES ('one','authorized');''')
    before=dbpath.read_bytes()
    with pytest.raises(ValueError):m.check_jobs(dbpath)
    assert dbpath.read_bytes()==before
    with sqlite3.connect(dbpath) as db:db.execute("INSERT INTO execution_jobs VALUES ('one','outcome_unknown')")
    with pytest.raises(ValueError):m.check_jobs(dbpath)
    with sqlite3.connect(dbpath) as db:db.execute("UPDATE execution_jobs SET state='succeeded'")
    m.check_jobs(dbpath)


def test_groups_are_explicit_and_root_equivalent_memberships_are_refused():
    from admin_executor import worker_maintenance as m
    assert m.parse_groups('27,1000')==[27,1000]
    for raw in ('','0,1000','1000,27','1000,1000','users','-1'):
        with pytest.raises(ValueError):m.parse_groups(raw)


def test_worker_quiescence_requires_finished_correlated_invocation(tmp_path):
    from admin_executor import worker_maintenance as m
    path=tmp_path/'auth.sqlite'
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE execution_jobs(job_id,state);
          CREATE TABLE execution_workers(job_id,invocation);
          INSERT INTO execution_jobs VALUES ('one','succeeded');
          INSERT INTO execution_workers VALUES ('one','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');''')
    assert m.worker_quiescent({'LoadState':'not-found'},path)
    assert m.worker_quiescent({'LoadState':'loaded','ActiveState':'inactive','Job':''},path)
    base={'LoadState':'loaded','ActiveState':'active','SubState':'exited','InvocationID':'a'*32,'Job':''}
    assert m.worker_quiescent(base,path)
    assert not m.worker_quiescent(base|{'SubState':'running'},path)
    assert not m.worker_quiescent(base|{'InvocationID':'b'*32},path)
    assert not m.worker_quiescent(base|{'Job':'12 /job/12'},path)


def test_source_snapshot_rejects_links_and_changed_manifest(tmp_path):
    from admin_executor import worker_maintenance as m
    (tmp_path/'bootstrap.py').write_text('MODULES=("bootstrap.py","restart.py")')
    (tmp_path/'restart.py').symlink_to('/etc/passwd')
    with pytest.raises((ValueError,OSError)):m.snapshot(tmp_path)


@pytest.fixture
def installation(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from admin_executor import worker_maintenance as m
    files,digest=m.snapshot(Path('admin_executor'))
    prefix=tmp_path/'prefix';package=prefix/'admin_executor';config=tmp_path/'config';state=tmp_path/'state';units=tmp_path/'units'
    for p in (package,config,state,units):p.mkdir(parents=True)
    for name,data in files.items():
        path=package/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    identity={'uid':1000,'gid':1000}
    for name,data in {'desktop-identity.json':json.dumps(identity),'catalogue.json':'{"unchanged":true}',
                      'origin':'https://admin.test','peers.json':'{}','health.json':'{}','return-url':'https://app.test'}.items():
        (config/name).write_text(data)
    for name in m.SERVICES:(units/name).write_text('UNCHANGED UNIT')
    database=state/'authorization.sqlite'
    with sqlite3.connect(database) as db:
        db.executescript('''CREATE TABLE authorized_jobs(id,state); CREATE TABLE execution_jobs(job_id,state);
          CREATE TABLE credentials(secret); INSERT INTO credentials VALUES ('preserved-passkey');''')
    for name,value in {'PREFIX':prefix,'PACKAGE':package,'CONFIG':config,'STATE':state,'UNITS':units,'POLICY':prefix/'bridge-worker.json'}.items():
        monkeypatch.setattr(m,name,value)
    monkeypatch.setattr(m,'protected',lambda *a,**k:None)
    monkeypatch.setattr(m,'inspect_host',lambda *a:None)
    calls=[]
    def command(*args):
        calls.append(args)
        return SimpleNamespace(stdout='LoadState=not-found\n' if 'show' in args else '')
    monkeypatch.setattr(m,'command',command)
    args=SimpleNamespace(installed_sha256=digest,bundle_sha256=digest)
    return m,files,args,identity,calls


def test_maintenance_preserves_passkeys_catalogue_jobs_and_other_units(installation):
    m,files,args,identity,calls=installation
    old_catalogue=(m.CONFIG/'catalogue.json').read_bytes()
    m.apply(files,args,m.render_unit(files['worker_units.py'],1000,1000),identity,[27,1000])
    assert (m.UNITS/m.WORKER).is_file()
    assert all((m.UNITS/name).read_text()=='UNCHANGED UNIT' for name in m.SERVICES)
    assert (m.CONFIG/'catalogue.json').read_bytes()==old_catalogue
    with sqlite3.connect(m.STATE/'authorization.sqlite') as db:
        assert db.execute('SELECT secret FROM credentials').fetchone()==('preserved-passkey',)
        assert db.execute('SELECT COUNT(*) FROM authorized_jobs').fetchone()==(0,)
    assert json.loads(m.POLICY.read_text())=={'uid':1000,'gid':1000,'groups':[27,1000]}
    assert not any('enable' in call for call in calls)
    assert not any('start' in call and m.WORKER in call for call in calls)
    assert not any('restart' in call for call in calls)
    assert (m.PREFIX/('admin_executor.before-worker-'+args.installed_sha256)).is_dir()


def test_maintenance_refuses_changed_pin_or_pending_job_before_stop(installation):
    m,files,args,identity,calls=installation
    args.installed_sha256='0'*64
    with pytest.raises(ValueError,match='bundle changed'):
        m.apply(files,args,'fixed unit',identity,[1000])
    assert calls==[]


def test_maintenance_failure_after_swap_keeps_admin_stopped_and_never_restores_auth(installation,monkeypatch):
    m,files,args,identity,calls=installation
    original=m.replace
    def fail(path,data,mode):
        if path==m.UNITS/m.WORKER: raise OSError('simulated write failure')
        return original(path,data,mode)
    monkeypatch.setattr(m,'replace',fail)
    with pytest.raises(OSError):m.apply(files,args,'fixed unit',identity,[1000])
    assert calls[-1]==('/usr/bin/systemctl','stop',*m.SERVICES)
    with sqlite3.connect(m.STATE/'authorization.sqlite') as db:
        assert db.execute('SELECT secret FROM credentials').fetchone()==('preserved-passkey',)
    assert (m.STATE/('worker-maintenance-'+args.installed_sha256)/'authorization.sqlite').is_file()


def test_maintenance_does_not_stop_services_while_execution_lock_is_held(installation):
    import fcntl
    m,files,args,identity,calls=installation
    with open((m.STATE/'authorization.sqlite').with_suffix('.execution.lock'),'w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):m.apply(files,args,'fixed',identity,[1000])
    assert not any('stop' in call for call in calls)


def test_new_authorization_during_freeze_aborts_and_restores_original_services(installation,monkeypatch):
    m,files,args,identity,calls=installation
    original=m.command
    def command(*argv):
        if 'stop' in argv:
            with sqlite3.connect(m.STATE/'authorization.sqlite') as db:
                db.execute("INSERT INTO authorized_jobs VALUES ('late','authorized')")
        return original(*argv)
    monkeypatch.setattr(m,'command',command)
    with pytest.raises(ValueError,match='Pending authorizations'):
        m.apply(files,args,'fixed',identity,[1000])
    assert calls[-1]==('/usr/bin/systemctl','start',*m.SERVICES)
    assert not m.POLICY.exists()


@pytest.fixture
def activation_69330af(tmp_path):
    """Reconstruct the exact public activation bundle without Git in CI.

    Unchanged files reuse current bytes only when their pinned hashes match;
    reviewed historical overrides retain the actual installed bytes. Thus this
    also works with GitHub Actions' shallow checkout, not just a local history.
    """
    import hashlib
    from admin_executor import worker_maintenance as m
    fixture=json.loads(Path('tests/fixtures/admin_activation_69330af.json').read_text())
    source=Path('admin_executor')
    installed=tmp_path/'activation-69330af';installed.mkdir()
    for name in fixture['modules']:
        data=fixture['overrides'][name].encode() if name in fixture['overrides'] else (source/name).read_bytes()
        assert hashlib.sha256(data).hexdigest()==fixture['sha256'][name],name
        target=installed/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
    old,digest=m.snapshot(installed)
    assert digest==fixture['bundle_sha256']=='580f9b4d63ab971b7059e1f5bc96e4d0bb78d196cf93579521ba8c04044324cb'
    return old


def test_activation_bundle_upgrades_with_closed_diagnostics_ui(activation_69330af):
    from admin_executor import worker_maintenance as m
    new,_=m.snapshot(Path('admin_executor'))
    old=activation_69330af
    assert old['web_static/app.js']!=new['web_static/app.js']
    assert old['web_static/index.html']!=new['web_static/index.html']
    m.validate_changes(old,new)


@pytest.mark.parametrize('name',['authorization.py','control.py','serve.py'])
def test_activation_upgrade_still_rejects_unrelated_changes(activation_69330af,name):
    from admin_executor import worker_maintenance as m
    new,_=m.snapshot(Path('admin_executor'))
    with pytest.raises(ValueError,match='outside the worker maintenance scope'):
        m.validate_changes(activation_69330af,new|{name:new[name]+b'\n# unrelated mutation\n'})


def test_operator_probe_is_not_part_of_the_installed_bundle():
    from admin_executor import worker_maintenance as m
    from admin_executor.bootstrap import MODULES
    files,_=m.snapshot(Path('admin_executor'))
    assert 'bridge_probe.py' not in files and 'bridge_probe.py' not in MODULES
    assert 'bridge_probe.py' not in m.CHANGES


def test_apply_from_exact_activation_snapshot_installs_diagnostics_without_changing_enrollment(installation,activation_69330af):
    import shutil
    m,new,args,identity,calls=installation
    shutil.rmtree(m.PACKAGE);m.PACKAGE.mkdir()
    for name,data in activation_69330af.items():
        target=m.PACKAGE/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
    old,digest=m.snapshot(m.PACKAGE)
    args.installed_sha256=digest
    assert old==activation_69330af
    assert 'bridge_probe.py' not in old and 'bridge_probe.py' not in new
    m.apply(new,args,m.render_unit(new['worker_units.py'],1000,1000),identity,[27,1000])
    for name in ('web_static/app.js','web_static/index.html'):
        assert (m.PACKAGE/name).read_bytes()==new[name]
        assert (m.PREFIX/('admin_executor.before-worker-'+digest)/name).read_bytes()==old[name]
    with sqlite3.connect(m.STATE/'authorization.sqlite') as db:
        assert db.execute('SELECT secret FROM credentials').fetchone()==('preserved-passkey',)
        assert db.execute('SELECT COUNT(*) FROM authorized_jobs').fetchone()==(0,)
    assert not any('start' in c and m.WORKER in c for c in calls)
