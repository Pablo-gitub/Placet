"""Fixed system-worker contract; never invoke a service manager."""
import json
import subprocess

import pytest

from admin_executor.restart import RestartHandlers, SpawnFailure
from admin_executor.execution import Supervisor
from tests.test_admin_execution import store, authorize

OLD, NEW = 'a' * 32, 'b' * 32


def state(invocation='', active='inactive', report=None, **changes):
    data = dict(LoadState='loaded', ActiveState=active, SubState='exited' if active == 'active' else 'dead',
                InvocationID=invocation, Job='', Result='success', ExecMainStatus='0', ExecMainCode='1',
                StatusText=json.dumps({'invocation':invocation, 'report':report or {'state':'succeeded','code':'restart_confirmed'}}))
    data.update(changes)
    return ''.join(f'{k}={v}\n' for k, v in data.items())


class Manager:
    def __init__(self, *, after=None, error=None, before=None):
        self.calls = []
        self.reads = 0
        self.stopped = False
        self.after, self.error, self.before = after or state(NEW, 'active'), error, before or state()

    def __call__(self, argv, **options):
        self.calls.append((argv, options))
        assert not {'user','group','extra_groups'} & options.keys()
        if 'show' in argv:
            self.reads += 1
            if self.stopped:
                self.stopped = False
                return state()
            return self.before if self.reads == 1 else self.after
        if 'stop' in argv:
            self.stopped = True
        if 'start' in argv and self.error:
            raise self.error
        return ''


def handler(manager):
    return RestartHandlers(desktop_uid=1000, desktop_gid=1000, health_port=8000, run=manager)


def test_bridge_uses_only_fixed_system_unit_and_records_its_invocation(store):
    from admin_executor.bridge_worker import WORKER
    authorize(store, '1' * 32, action='restart-bridge')
    manager = Manager()
    outcome = Supervisor(store, enabled={'restart-bridge'}, handler=handler(manager)).run_once()
    assert outcome['state'] == 'succeeded'
    assert outcome['worker_invocation'] == NEW
    assert store.status('1' * 32)['worker_invocation'] == NEW
    starts = [c for c in manager.calls if 'start' in c[0]]
    assert len(starts) == 1 and starts[0][0] == ['/usr/bin/systemctl','--no-ask-password','start',WORKER]
    assert all(c[1]['env'] == {'PATH':'/usr/bin:/bin','LC_ALL':'C'} for c in manager.calls)


@pytest.mark.parametrize('error,code,state_name', [
    (SpawnFailure(1), 'worker_start_not_sent', 'failed'),
    (subprocess.TimeoutExpired('fixed',75), 'worker_dispatch_uncertain', 'outcome_unknown'),
    (subprocess.CalledProcessError(1,'fixed',stderr='SECRET'), 'worker_dispatch_uncertain', 'outcome_unknown'),
    (OSError('SECRET'), 'worker_dispatch_uncertain', 'outcome_unknown'),
])
def test_worker_dispatch_error_never_retries_or_leaks_output(store, error, code, state_name):
    authorize(store, '1' * 32, action='restart-bridge')
    manager = Manager(error=error)
    worker = Supervisor(store, enabled={'restart-bridge'}, handler=handler(manager))
    result = worker.run_once()
    assert (result['state'],result['result_code']) == (state_name,code)
    assert worker.run_once() is None
    assert sum('start' in c[0] for c in manager.calls) == 1
    assert 'SECRET' not in json.dumps(result)


@pytest.mark.parametrize('after,code', [
    (state('', 'active'), 'worker_invocation_invalid'),
    (state(NEW,'failed'), 'worker_execution_uncertain'),
    (state(NEW,'active',ExecMainStatus='1'), 'worker_execution_uncertain'),
    (state(NEW,'active',StatusText='SECRET'), 'worker_report_invalid'),
    (state(NEW,'active',StatusText=json.dumps({'invocation':OLD,'report':{'state':'succeeded','code':'restart_confirmed'}})), 'worker_report_invalid'),
    (state(NEW,'active',report={'state':'failed','code':'manager_unreachable'}), 'worker_execution_uncertain'),
])
def test_worker_requires_new_invocation_and_correlated_closed_report(after,code):
    result = handler(Manager(after=after))('restart-bridge')
    assert str(result) == 'outcome_unknown' and result.code == code


def test_worker_busy_is_never_started_or_stopped(store):
    authorize(store, '1' * 32, action='restart-bridge')
    manager = Manager(before=state(OLD,'active'))
    result = Supervisor(store, enabled={'restart-bridge'}, handler=handler(manager)).run_once()
    assert result['state'] == 'outcome_unknown' and result['result_code'] == 'worker_busy'
    assert len(manager.calls) == 1


def test_crash_after_worker_dispatch_retains_gate_without_replay(store):
    authorize(store, '1' * 32, action='restart-bridge')
    manager = Manager()
    original = manager.__class__.__call__
    def crash(self,argv,**options):
        if 'start' in argv:
            raise KeyboardInterrupt()
        return original(self,argv,**options)
    from unittest.mock import patch
    with patch.object(Manager,'__call__',crash), pytest.raises(KeyboardInterrupt):
        Supervisor(store,enabled={'restart-bridge'},handler=handler(manager)).run_once()
    assert Supervisor(store,enabled={'restart-bridge'},handler=handler(manager)).run_once() is None
    assert store.status('1' * 32)['state'] == 'outcome_unknown'


def test_previous_completed_worker_is_stopped_only_with_durable_correlation(store):
    authorize(store, '1' * 32, action='restart-bridge')
    assert Supervisor(store,enabled={'restart-bridge'},handler=handler(Manager())).run_once()['state']=='succeeded'
    authorize(store, '2' * 32, action='restart-bridge')
    manager = Manager(before=state(NEW,'active'),after=state(OLD,'active'))
    assert Supervisor(store,enabled={'restart-bridge'},handler=handler(manager)).run_once()['state']=='succeeded'
    assert sum('stop' in c[0] for c in manager.calls)==1


def test_worker_unit_is_fixed_unprivileged_and_has_no_automatic_restart():
    from admin_executor.worker_units import worker_unit
    text = worker_unit(uid=1000,gid=1000)
    for setting in ('User=1000','Group=1000','NoNewPrivileges=yes','CapabilityBoundingSet=\n',
                    'AmbientCapabilities=\n','Restart=no','NotifyAccess=main','RemainAfterExit=yes',
                    'InaccessiblePaths=-/home -/root -/var/lib/placet-admin'):
        assert setting in text
    assert 'ExecStart=/usr/local/lib/placet-admin-venv/bin/python -I /usr/local/lib/placet-admin/admin_executor/bridge_restart.py' in text
    assert '[Install]' not in text and 'ReadWritePaths=' not in text
    with pytest.raises(ValueError):
        worker_unit(uid=0,gid=1000)


def test_worker_contract_checks_every_identity_and_capability_before_acting():
    from admin_executor.bridge_restart import verify_identity
    policy = {'uid':1000,'gid':1000,'groups':[27,1000]}
    fields = dict(Uid='1000 1000 1000 1000',Gid='1000 1000 1000 1000',Groups='27 1000',NoNewPrivs='1',
                  **{k:'0' for k in ('CapInh','CapPrm','CapEff','CapBnd','CapAmb')})
    verify_identity(policy,fields,group_name=lambda g:'sudo' if g==27 else 'users')
    for key,value in [('Uid','1000 0 1000 1000'),('Gid','1000 1000 0 1000'),('Groups','27 999 1000'),
                      ('NoNewPrivs','0'),('CapBnd','80'),('CapEff','40')]:
        with pytest.raises(ValueError):
            verify_identity(policy,fields|{key:value},group_name=lambda g:'users')
    with pytest.raises(ValueError):
        verify_identity(policy,fields,group_name=lambda g:'docker')


def test_worker_notification_has_only_invocation_and_closed_report(monkeypatch):
    from admin_executor import bridge_restart
    sent=[]
    class Channel:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def settimeout(self,n): assert n==2
        def sendto(self,data,address): sent.append((data,address))
        def sendmsg(self,buffers,ancillary,flags,address):
            assert len(sent)==1 and sent[0][0].startswith(b'STATUS=')
            assert buffers==[b'BARRIER=1'] and len(ancillary)==1
            return len(buffers[0])
    monkeypatch.setenv('INVOCATION_ID',NEW)
    monkeypatch.setenv('NOTIFY_SOCKET','@fixed-notify')
    monkeypatch.setattr(bridge_restart.socket,'socket',lambda *a:Channel())
    bridge_restart.notify('{"state":"succeeded","code":"restart_confirmed"}')
    message,address=sent[0]
    assert address=='\0fixed-notify'
    data=json.loads(message.decode().removeprefix('STATUS='))
    assert set(data)=={'invocation','report'} and data['invocation']==NEW
    assert len(message)<1200


def test_unknown_worker_invocation_is_not_reused_for_another_job(store):
    authorize(store,'1'*32,action='restart-bridge')
    broken=Manager(after=state(NEW,'active',StatusText='bad'))
    assert Supervisor(store,enabled={'restart-bridge'},handler=handler(broken)).run_once()['state']=='outcome_unknown'
    authorize(store,'2'*32,action='restart-bridge')
    manager=Manager(before=state(NEW,'active'))
    assert Supervisor(store,enabled={'restart-bridge'},handler=handler(manager)).run_once() is None
    assert manager.calls==[]


@pytest.mark.parametrize('mode',['ack','timeout','send_error','unexpected_data'])
def test_notification_barrier_waits_for_manager_and_closes_pipe(monkeypatch,mode):
    import array
    import os
    from admin_executor import bridge_restart as worker
    events=[]; descriptors=[]; remote=[]
    class Channel:
        def sendmsg(self,buffers,ancillary,flags,address):
            events.append('barrier')
            assert buffers==[b'BARRIER=1'] and flags==0 and address=='\0notify'
            level,kind,data=ancillary[0]
            assert len(ancillary)==1 and level==worker.socket.SOL_SOCKET and kind==worker.socket.SCM_RIGHTS
            fds=array.array('i');fds.frombytes(bytes(data))
            assert len(fds)==1
            descriptors.append(fds[0]);remote.append(os.dup(fds[0]))
            if mode=='send_error': raise OSError('simulated send failure')
            return len(b'BARRIER=1')
    def select(reads,writes,errors,timeout):
        events.append('wait')
        assert writes==errors==[] and 0<=timeout<=3
        descriptors.extend(reads)
        with pytest.raises(OSError):os.fstat(descriptors[0])
        if mode=='timeout':return ([],[],[])
        if mode=='unexpected_data':os.write(remote[0],b'x')
        os.close(remote.pop())
        return (reads,[],[])
    monkeypatch.setattr(worker.select,'select',select)
    try:
        if mode=='ack':worker.notification_barrier(Channel(),'\0notify')
        else:
            with pytest.raises((TimeoutError,OSError,ValueError)):
                worker.notification_barrier(Channel(),'\0notify')
        assert events==(['barrier'] if mode=='send_error' else ['barrier','wait'])
        for fd in descriptors:
            with pytest.raises(OSError):os.fstat(fd)
    finally:
        for fd in remote:os.close(fd)


def test_worker_timeout_leaves_headroom_for_inner_calls_and_barrier():
    from admin_executor.worker_units import worker_unit
    manager=Manager()
    assert handler(manager)('restart-bridge')=='succeeded'
    start=next(options for argv,options in manager.calls if 'start' in argv)
    assert 'TimeoutStartSec=60' in worker_unit(uid=1000,gid=1000)
    assert start['timeout']==75
