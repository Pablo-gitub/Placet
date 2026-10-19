"""Protected root supervisor entry point; installation remains a separate step.

No socket, shell, HTTP endpoint or command-line parameters. Catalogue and
identity are fixed root-owned files. The authorization broker is separate work;
this process can only consume previously committed, protected authorizations.
"""
import json
import os
from pathlib import Path
import stat
import sys
import time

from .execution import ACTIONS, ExecutionStore, Supervisor
from .restart import RestartHandlers


CONFIG = Path('/etc/placet-admin')
STATE = Path('/var/lib/placet-admin')


def enabled_actions(catalogue):
    if (not isinstance(catalogue, dict) or set(catalogue) != {'revision', 'actions'}
            or type(catalogue['revision']) is not int or catalogue['revision'] != 1
            or not isinstance(catalogue['actions'], list)):
        raise ValueError('Unsupported protected catalogue')
    enabled, seen = set(), set()
    for entry in catalogue['actions']:
        if (not isinstance(entry, dict) or set(entry) != {'id', 'enabled'}
                or not isinstance(entry['id'], str) or entry['id'] not in ACTIONS | {'deploy'}
                or type(entry['enabled']) is not bool or entry['id'] in seen):
            raise ValueError('Invalid protected action')
        seen.add(entry['id'])
        if entry['enabled']:
            if entry['id'] not in ACTIONS:
                raise ValueError('No executable deploy handler')
            enabled.add(entry['id'])
    return frozenset(enabled)


def configuration(catalogue, identity):
    enabled = enabled_actions(catalogue)
    if (not isinstance(identity, dict) or set(identity) != {'uid', 'gid'}
            or any(type(identity[k]) is not int or identity[k] <= 0 for k in ('uid', 'gid'))):
        raise ValueError('Explicit unprivileged desktop identity required')
    return enabled, identity['uid'], identity['gid']


def protected_file(path):
    for parent in path.parents:
        metadata = parent.lstat()
        if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0
                or metadata.st_mode & 0o022):
            raise ValueError('Unsafe administrative directory')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        metadata = os.fstat(stream.fileno())
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0
                or metadata.st_mode & 0o022 or metadata.st_size > 65536):
            raise ValueError('Unsafe administrative file')
        data = stream.read(65537)
        if len(data) > 65536:
            raise ValueError('Administrative configuration too large')
        return data


def validate_directory(path, *, private, owner=0):
    info = path.lstat()
    forbidden = 0o077 if private else 0o022
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != owner or info.st_mode & forbidden:
        raise ValueError('Unsafe administrative state directory')


def main():
    if os.geteuid() != 0:
        raise ValueError('The protected supervisor requires root')
    if len(sys.argv) != 1:
        raise ValueError('The supervisor accepts no arguments')
    # Require installation under the protected prefix before touching state.
    module = Path('/usr/local/lib/placet-admin/admin_executor/supervisor.py')
    if Path(__file__) != module:
        raise ValueError('Protected supervisor installation required')
    protected_file(module)
    database = STATE / 'authorization.sqlite'
    # The DB may grow beyond config limits. Only inspect metadata here.
    validate_directory(STATE, private=True)
    for parent in STATE.parents:
        validate_directory(parent, private=False)
    info = database.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise ValueError('Private authorization database required')
    store = ExecutionStore(database)
    while True:
        # Re-read catalogue before each claim: disabling an action takes effect
        # without a daemon restart. A handler already running is not cancelled.
        enabled, uid, gid = configuration(json.loads(protected_file(CONFIG / 'catalogue.json')),
                                          json.loads(protected_file(CONFIG / 'desktop-identity.json')))
        Supervisor(store, enabled=enabled, handler=RestartHandlers(desktop_uid=uid, desktop_gid=gid)).run_once()
        time.sleep(1)


if __name__ == '__main__':
    main()
