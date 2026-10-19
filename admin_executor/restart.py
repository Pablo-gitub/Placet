"""Fixed restart handlers. No requester-supplied argv, environment or unit names."""
import subprocess
import time
from http.client import HTTPConnection, HTTPException


ENV = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'}
BACKEND = 'placet-backend.service'
BRIDGE = 'placet-bridge.service'


def run_fixed(argv, **options):
    result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, check=True, **options)
    if len(result.stdout) > 8192:
        raise ValueError('Unexpected service response')
    return result.stdout.decode('utf-8', errors='strict')


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
        return 'failed'
    try:
        run(prefix + ['restart', unit], timeout=15, env=options['env'])
        after = status()
        if (after.get('ActiveState') == 'active' and after.get('SubState') == 'running'
                and after.get('InvocationID') and after['InvocationID'] != before.get('InvocationID')):
            if user:
                return 'succeeded'
            pause(5)
            stable = status()
            if (stable.get('ActiveState') == 'active' and stable.get('SubState') == 'running'
                    and stable.get('InvocationID') == after['InvocationID']
                    and stable.get('NRestarts', '').isdigit()
                    and stable['NRestarts'] == after.get('NRestarts') and health()):
                return 'succeeded'
            return 'failed'
    except Exception:
        pass
    return 'outcome_unknown'


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
            # Python drops supplemental groups, GID and UID before exec. The
            # user-controlled bus and service definitions are never read as root.
            result = self.run(['/usr/bin/python3', '-I',
                               '/usr/local/lib/placet-admin/admin_executor/bridge_restart.py'],
                              timeout=40, env=env, user=self.uid, group=self.gid, extra_groups=[])
            if result.strip() in ('succeeded', 'failed', 'outcome_unknown'):
                return result.strip()
        except Exception:
            pass
        return 'outcome_unknown'
