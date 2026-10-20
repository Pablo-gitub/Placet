"""Fixed restart handlers. No requester-supplied argv, environment or unit names."""
import json
import os
import select
import subprocess
import time
from http.client import HTTPConnection, HTTPException


ENV = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'}
BACKEND = 'placet-backend.service'
BRIDGE = 'placet-bridge.service'


# Closed pairs: a nonzero manager command does not prove it had no effect.
RESULT_CODES = {
    'helper_spawn_failed': 'failed', 'helper_import_failed': 'failed',
    'helper_exit_nonzero': 'outcome_unknown', 'helper_timeout': 'outcome_unknown',
    'helper_response_invalid': 'outcome_unknown', 'helper_io_failed': 'outcome_unknown',
    'manager_unreachable': 'failed',
    'manager_response_invalid': 'failed', 'restart_not_sent': 'failed',
    'restart_rejected': 'outcome_unknown', 'restart_timeout': 'outcome_unknown',
    'restart_unconfirmed': 'outcome_unknown', 'verification_failed': 'outcome_unknown',
    'invocation_unchanged': 'outcome_unknown', 'restart_confirmed': 'succeeded',
    'backend_unhealthy': 'failed', 'handler_failed': 'outcome_unknown',
    'invalid_result': 'outcome_unknown',
}


class RestartResult(str):
    """A terminal state plus a bounded, allowlisted phase code."""
    def __new__(cls, state, code):
        if not isinstance(code, str) or RESULT_CODES.get(code) != state:
            raise ValueError('Unsupported restart result')
        result = super().__new__(cls, state)
        result.code = code
        return result

    def encode_report(self):
        return json.dumps({'state': str(self), 'code': self.code}, separators=(',', ':'))


def result(code):
    return RestartResult(RESULT_CODES[code], code)


def decode_report(raw):
    if not isinstance(raw, str) or len(raw) > 1024:
        raise ValueError('Invalid helper response')
    payload = json.loads(raw)
    if not isinstance(payload, dict) or set(payload) != {'state', 'code'}:
        raise ValueError('Invalid helper envelope')
    if type(payload['state']) is not str or type(payload['code']) is not str:
        raise ValueError('Invalid helper fields')
    # Only phases emitted inside the unprivileged helper are accepted here.
    allowed = {'helper_import_failed', 'manager_unreachable', 'manager_response_invalid',
               'restart_not_sent', 'restart_rejected', 'restart_timeout', 'restart_unconfirmed',
               'verification_failed', 'invocation_unchanged', 'restart_confirmed'}
    if payload['code'] not in allowed:
        raise ValueError('Invalid helper phase')
    return RestartResult(payload['state'], payload['code'])


class SpawnFailure(subprocess.SubprocessError):
    """Only Popen construction failed, before the runner collected any output."""
    def __init__(self, number=None):
        super().__init__('Fixed process could not be started')
        self.errno = number


def run_fixed(argv, **options):
    """Bound stdout during collection; never retain child stderr in exceptions."""
    timeout = options.pop('timeout')
    deadline = time.monotonic() + timeout
    try:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, **options)
    except (OSError, subprocess.SubprocessError) as error:
        raise SpawnFailure(getattr(error, 'errno', None)) from None
    output = bytearray()
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([process.stdout], [], [], remaining)[0]:
                raise subprocess.TimeoutExpired(argv, timeout)
            chunk = os.read(process.stdout.fileno(), 4096)
            if not chunk:
                break
            output.extend(chunk)
            if len(output) > 8192:
                raise ValueError('Service response exceeds limit')
        returncode = process.wait(timeout=max(0, deadline - time.monotonic()))
        if returncode:
            raise subprocess.CalledProcessError(returncode, argv)
        return output.decode('utf-8', errors='strict')
    except BaseException:
        if process.poll() is None:
            process.kill()
        process.wait()
        raise
    finally:
        process.stdout.close()


def backend_health(port):
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('Protected health port required')
    connection = HTTPConnection('127.0.0.1', port, timeout=2)
    try:
        connection.request('GET', '/api/health')
        return connection.getresponse().status == 200
    except (OSError, HTTPException):
        return False
    finally:
        connection.close()


def restart_service(unit, *, user=False, run=run_fixed, env=None, pause=time.sleep, health=None):
    """A changed active invocation confirms restart, not application readiness.

    No retry after timeout: the manager may still be processing RestartUnit.
    The independent supervisor records uncertainty and leaves reconciliation to
    an operator. No additional service is started if the user bus is missing.
    """
    if (unit, user) not in ((BACKEND, False), (BRIDGE, True)):
        raise ValueError('Unknown fixed service')
    if user:
        return restart_user_service(run=run, env=env, pause=pause)
    if not user and health is None:
        raise ValueError('Configured backend health check required')
    prefix = ['/usr/bin/systemctl'] + (['--user'] if user else [])
    options = dict(timeout=10, env=ENV.copy() if env is None else env)
    show = prefix + ['show', unit, '--property=ActiveState,SubState,InvocationID,NRestarts']
    def status():
        return dict(line.split('=', 1) for line in run(show, **options).splitlines() if '=' in line)
    # Failure before the restart is safe to report as failed. Once requested,
    # failure or timeout means its effect is unknown, never blindly repeat it.
    try:
        before = status()
    except Exception:
        return result('manager_unreachable')
    try:
        run(prefix + ['restart', unit], timeout=15, env=options['env'])
        after = status()
        if (after.get('ActiveState') == 'active' and after.get('SubState') == 'running'
                and after.get('InvocationID') and after['InvocationID'] != before.get('InvocationID')):
            pause(5)
            stable = status()
            if (stable.get('ActiveState') == 'active' and stable.get('SubState') == 'running'
                    and stable.get('InvocationID') == after['InvocationID']
                    and stable.get('NRestarts', '').isdigit()
                    and stable['NRestarts'] == after.get('NRestarts') and health()):
                return result('restart_confirmed')
            return result('backend_unhealthy')
    except Exception:
        pass
    return result('verification_failed')


def restart_user_service(*, run, env, pause):
    """Fixed bridge operation: failure is certain only before possible dispatch."""
    options = dict(timeout=5, env=ENV.copy() if env is None else env)
    show = ['/usr/bin/systemctl', '--user', 'show', BRIDGE,
            '--property=ActiveState,SubState,InvocationID,NRestarts']
    def status():
        text = run(show, **options)
        if not isinstance(text, str) or len(text) > 8192:
            raise ValueError('Invalid manager response')
        fields = dict(line.split('=', 1) for line in text.splitlines() if '=' in line)
        if (fields.get('ActiveState') not in {'active', 'inactive', 'failed', 'activating', 'deactivating', 'reloading', 'maintenance', 'refreshing'}
                or not fields.get('SubState') or len(fields.get('InvocationID', '')) > 128):
            raise ValueError('Invalid manager status')
        return fields
    try:
        before = status()
    except ValueError:
        return result('manager_response_invalid')
    except Exception:
        return result('manager_unreachable')
    try:
        run(['/usr/bin/systemctl', '--user', 'restart', BRIDGE], timeout=15, env=options['env'])
    except subprocess.TimeoutExpired:
        return result('restart_timeout')
    except subprocess.CalledProcessError:
        return result('restart_rejected')
    except SpawnFailure:
        return result('restart_not_sent')
    except Exception:
        return result('restart_unconfirmed')
    try:
        after = status()
        invocation = after.get('InvocationID')
        if invocation and invocation == before.get('InvocationID'):
            return result('invocation_unchanged')
        if not invocation or after['ActiveState'] != 'active' or after['SubState'] != 'running':
            return result('verification_failed')
        pause(2)
        stable = status()
        if (stable.get('InvocationID') != invocation or stable['ActiveState'] != 'active'
                or stable['SubState'] != 'running' or not stable.get('NRestarts', '').isdigit()
                or stable['NRestarts'] != after.get('NRestarts')):
            return result('verification_failed')
        return result('restart_confirmed')
    except Exception:
        return result('verification_failed')


class RestartHandlers:
    def __init__(self, *, desktop_uid, desktop_gid, health_port, run=run_fixed, pause=time.sleep, health=None):
        if (type(desktop_uid) is not int or type(desktop_gid) is not int
                or desktop_uid <= 0 or desktop_gid <= 0):
            raise ValueError('Explicit unprivileged desktop identity required')
        if type(health_port) is not int or not 1 <= health_port <= 65535:
            raise ValueError('Protected health port required')
        self.uid, self.gid, self.run = desktop_uid, desktop_gid, run
        self.pause, self.health = pause, health if health is not None else lambda: backend_health(health_port)

    def __call__(self, action):
        if action == 'restart-backend':
            return restart_service(BACKEND, run=self.run, pause=self.pause, health=self.health)
        if action != 'restart-bridge':
            raise ValueError('Unknown reserved action')
        env = {**ENV, 'XDG_RUNTIME_DIR': f'/run/user/{self.uid}',
               'DBUS_SESSION_BUS_ADDRESS': f'unix:path=/run/user/{self.uid}/bus'}
        try:
            # Drop all supplemental groups/GID/UID before exec, never as root.
            raw = self.run(['/usr/bin/python3', '-I',
                            '/usr/local/lib/placet-admin/admin_executor/bridge_restart.py'],
                           timeout=40, env=env, user=self.uid, group=self.gid, extra_groups=[])
        except subprocess.TimeoutExpired:
            return result('helper_timeout')
        except subprocess.CalledProcessError:
            return result('helper_exit_nonzero')
        except SpawnFailure:
            return result('helper_spawn_failed')
        except OSError:
            return result('helper_io_failed')
        except Exception:
            return result('helper_response_invalid')
        try:
            return decode_report(raw)
        except (ValueError, TypeError):
            return result('helper_response_invalid')
