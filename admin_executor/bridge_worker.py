"""Root-side fixed system-manager adapter. No UID drop or user-written files."""
import json
import re

from .restart import ENV, SpawnFailure, decode_report, result
from .worker_units import WORKER

HEX = re.compile(r'^[0-9a-f]{32}$')
PROPERTIES = '--property=LoadState,ActiveState,SubState,InvocationID,Job,Result,ExecMainStatus,ExecMainCode,StatusText'


def restart_worker(*, run, record=None):
    def command(verb, timeout=5):
        argv = ['/usr/bin/systemctl','--no-ask-password',verb,WORKER]
        if verb == 'show':
            argv.append(PROPERTIES)
        return run(argv, timeout=timeout, env=ENV.copy())

    def status():
        raw = command('show')
        if not isinstance(raw,str) or len(raw) > 8192:
            raise ValueError('Invalid system worker status')
        fields = dict(line.split('=',1) for line in raw.splitlines() if '=' in line)
        if fields.get('LoadState') not in {'loaded','not-found'}:
            raise ValueError('Invalid worker load state')
        return fields

    try:
        before = status()
    except Exception:
        return result('worker_manager_unreachable')
    if before['LoadState'] == 'not-found':
        return result('worker_not_installed')
    old = before.get('InvocationID','')
    if before.get('Job') or before.get('ActiveState') in {'activating','deactivating','reloading'}:
        return result('worker_busy')
    if before.get('ActiveState') not in {'inactive','failed','active'}:
        return result('worker_busy')
    # Only a durably finished/reconciled previous attempt may be cleared.
    # A running or untracked worker might still restart the target: never stop it.
    if old or before.get('ActiveState') != 'inactive':
        if (not HEX.fullmatch(old) or before.get('SubState') not in {'exited','dead','failed'}
                or record is None or not record('previous',old)):
            return result('worker_busy')
        try:
            command('stop',timeout=10)
            cleared = status()
            if cleared.get('ActiveState') != 'inactive' or cleared.get('Job'):
                return result('worker_busy')
        except Exception:
            return result('worker_busy')
    if record is not None:
        record('dispatch',None)
    try:
        command('start',timeout=75)
    except SpawnFailure:
        return result('worker_start_not_sent')
    except Exception:
        return result('worker_dispatch_uncertain')
    try:
        after = status()
        invocation = after.get('InvocationID','')
        if not HEX.fullmatch(invocation) or invocation == old:
            return result('worker_invocation_invalid')
        if record is not None:
            record('invocation',invocation)
        if (after.get('Job') or after.get('ActiveState') != 'active' or after.get('SubState') != 'exited'
                or after.get('Result') != 'success' or after.get('ExecMainStatus') != '0'
                or after.get('ExecMainCode') != '1'):
            return result('worker_execution_uncertain')
        raw = after.get('StatusText','')
        if len(raw) > 1200:
            raise ValueError('Worker report exceeds limit')
        envelope = json.loads(raw)
        if (not isinstance(envelope,dict) or set(envelope) != {'invocation','report'}
                or envelope['invocation'] != invocation):
            raise ValueError('Uncorrelated worker report')
        report = decode_report(json.dumps(envelope['report']))
        # Even a worker-reported pre-dispatch failure is user-controlled
        # evidence after system-worker dispatch, not an independent no-effect proof.
        return report if str(report) != 'failed' else result('worker_execution_uncertain')
    except Exception:
        return result('worker_report_invalid')
