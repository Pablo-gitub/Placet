"""Reviewed upgrade from enrollment-only installation to two fixed restarts.

Preview is read-only. Applying requires root, source and installed-code pins,
explicit enablement and a host without desktop root-equivalent access. No pip,
shell, deploy, Tailscale changes, sudoers or account changes are performed.
"""
import argparse
import ast
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import types
from urllib.parse import urlsplit

PREFIX = Path('/usr/local/lib/placet-admin')
PACKAGE = PREFIX / 'admin_executor'
CONFIG = Path('/etc/placet-admin')
STATE = Path('/var/lib/placet-admin')
UNITS = Path('/etc/systemd/system')
VENV = Path('/usr/local/lib/placet-admin-venv')
SERVICES = ('placet-admin-control.service', 'placet-admin-supervisor.service')


def snapshot(source):
    """Freeze bounded manifest data without importing checkout code or pyc."""
    def read(path):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 1048576:
                raise ValueError('Expected bounded regular source files')
            data = stream.read(1048577)
            if len(data) > 1048576:
                raise ValueError('Source exceeds limit')
            return data
    bootstrap = read(source / 'bootstrap.py')
    nodes = [n for n in ast.parse(bootstrap).body if isinstance(n, ast.Assign)
             and any(isinstance(t, ast.Name) and t.id == 'MODULES' for t in n.targets)]
    if len(nodes) != 1:
        raise ValueError('Invalid module manifest')
    names = ast.literal_eval(nodes[0].value)
    if not isinstance(names, tuple) or len(names) > 100 or len(set(names)) != len(names):
        raise ValueError('Invalid module manifest')
    files, digest = {}, hashlib.sha256()
    for name in names:
        if (not isinstance(name, str) or name.startswith('/') or '\\' in name
                or any(p in ('', '.', '..') for p in name.split('/'))):
            raise ValueError('Invalid source path')
        # Validate intermediate components too; O_NOFOLLOW covers the final file.
        for parent in (source / name).parents:
            if parent == source.parent:
                break
            if parent.is_symlink() or not parent.is_dir():
                raise ValueError('Source directories cannot be links')
        data = bootstrap if name == 'bootstrap.py' else read(source / name)
        files[name] = data
        digest.update(name.encode() + b'\0' + len(data).to_bytes(8, 'big') + data)
    return files, digest.hexdigest()


def frozen(files, name):
    module = types.ModuleType('reviewed_' + name)
    exec(compile(files[name + '.py'], name + '.py', 'exec'), module.__dict__)
    return module


def protected(path, *, directory=False, private=False):
    for ancestor in (path, *path.parents):
        info = ancestor.lstat()
        is_directory = ancestor != path or directory
        if (info.st_uid != 0 or info.st_mode & (0o077 if private and ancestor == path else 0o022)
                or not (stat.S_ISDIR(info.st_mode) if is_directory else stat.S_ISREG(info.st_mode))
                or 'system.posix_acl_access' in os.listxattr(ancestor, follow_symlinks=False)
                or (not is_directory and info.st_nlink != 1)):
            raise ValueError('Installation is not exclusively root-protected: ' + str(ancestor))


def checked_configuration(args):
    parsed = urlsplit(args.return_url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.port is not None or parsed.query or parsed.fragment or parsed.path not in ('', '/')
            or args.return_url not in ('https://' + parsed.hostname, 'https://' + parsed.hostname + '/')):
        raise ValueError('Return URL must be a fixed HTTPS application origin')
    if not 1 <= args.health_port <= 65535:
        raise ValueError('Invalid loopback health port')


def write_new(path, data, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'wb') as output:
        output.write(data)
        output.flush()
        os.fchmod(output.fileno(), mode)
        os.fsync(output.fileno())


def replace(path, data, mode=0o600):
    stage = path.with_name(path.name + '.activation-new')
    write_new(stage, data, mode)
    stage.replace(path)
    fd = os.open(path.parent, os.O_DIRECTORY | os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def run(*args):
    return subprocess.run(list(args), check=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=40, env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'}, text=True)


def apply(files, args):
    """First activation only: partial upgrades require a separate human review."""
    for path in (PREFIX, PACKAGE, CONFIG, STATE, VENV, UNITS):
        protected(path, directory=True, private=path in (CONFIG, STATE))
    for directory, folders, names in os.walk(VENV, followlinks=False):
        protected(Path(directory), directory=True)
        for name in names:
            protected(Path(directory) / name)
        for name in folders:
            protected(Path(directory) / name, directory=True)
    old, digest = snapshot(PACKAGE)
    if digest != args.installed_sha256:
        raise ValueError('Installed bundle changed; review its digest again')
    for name in old:
        protected(PACKAGE / name)
    for name in ('origin', 'catalogue.json'):
        protected(CONFIG / name)
    origin = (CONFIG / 'origin').read_text().strip()
    if origin == args.return_url.rstrip('/'):
        raise ValueError('Confirmation origin must differ from the PWA')
    disabled = {'revision': 1, 'actions': [{'id': action, 'enabled': False}
                for action in ('deploy', 'restart-backend', 'restart-bridge')]}
    if json.loads((CONFIG / 'catalogue.json').read_text()) != disabled:
        raise ValueError('Only an enrollment-only disabled installation can be activated')
    for path in [*(UNITS / name for name in SERVICES), *(CONFIG / name for name in
                 ('peers.json', 'desktop-identity.json', 'health.json', 'return-url')), STATE / 'activation-before.sqlite',
                 PREFIX / 'admin_executor.activation-stage', PREFIX / ('admin_executor.before-activation-' + digest)]:
        if os.path.lexists(path):
            raise ValueError('Existing activation or interrupted attempt needs human review: ' + str(path))
    protected(UNITS / 'placet-admin-web.service')
    # Do not replace a locally customized web service or systemd drop-in.
    tree = ast.parse(old['web_service.py'])
    value = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == 'UNIT' for t in n.targets))
    if (UNITS / 'placet-admin-web.service').read_text() != value:
        raise ValueError('Installed web unit differs from its reviewed package')
    for service in (*SERVICES, 'placet-admin-web.service'):
        for base in (Path('/etc/systemd/system'), Path('/run/systemd/system')):
            if (base / (service + '.d')).exists():
                raise ValueError('Administrative service drop-ins require explicit review')
    database = STATE / 'authorization.sqlite'
    protected(database)
    checks = frozen(files, 'activation_checks')
    desktop, web, backend = checks.inspect_host(args.desktop_user)
    # Privileged restarts must target an unprivileged backend, not a root unit.
    user = run('/usr/bin/systemctl', 'show', 'placet-backend.service', '--property=User', '--value').stdout.strip()
    if user != backend.pw_name:
        raise ValueError('Backend service must run as the protected backend account')
    # Inspect policy as root rather than using the operator's cached sudo
    # timestamp. Merely testing `sudo -n` could pass after the human just
    # authenticated this bootstrap, or miss policies requiring listing auth.
    policy = run('/usr/bin/sudo', '-l', '-U', desktop.pw_name).stdout
    if (len(policy) > 65536 or any(marker in policy for marker in
            ('NOPASSWD:', '!authenticate', 'exempt_group=', 'timestamp_type=global'))):
        raise ValueError('Desktop sudo policy permits unattended elevation or needs review')
    # Query only a root-owned DB. No web-owned SQLite file is opened as root.
    with sqlite3.connect('file:' + str(database) + '?mode=ro', uri=True) as db:
        if not db.execute('SELECT 1 FROM credentials WHERE revoked=0 LIMIT 1').fetchone():
            raise ValueError('Enroll a passkey before activation')
        if db.execute('SELECT 1 FROM authorized_jobs LIMIT 1').fetchone():
            raise ValueError('Existing authorizations require human review before first activation')
        row = db.execute('SELECT origin FROM identity WHERE singleton=1').fetchone()
        if row != (origin,):
            raise ValueError('Enrolled passkey origin differs from protected configuration')
    # All guards above precede mutations and service changes. Keep this lock
    # across code/config installation; a second bootstrap cannot overlap.
    lock = os.open(STATE / '.activation.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (snapshot(PACKAGE)[1] != digest
                or json.loads((CONFIG / 'catalogue.json').read_text()) != disabled
                or (STATE / 'activation-before.sqlite').exists()
                or any((UNITS / name).exists() for name in SERVICES)):
            raise ValueError('Installation changed during preflight')
        run('/usr/bin/systemctl', 'stop', 'placet-admin-web.service')
        # Freeze a consistent root-private snapshot, preserving live enrollment.
        write_new(STATE / 'activation-before.sqlite', b'')
        with sqlite3.connect(database) as incoming, sqlite3.connect(STATE / 'activation-before.sqlite') as backup:
            incoming.backup(backup)
        database.chmod(0o600)
        stage = PREFIX / 'admin_executor.activation-stage'
        stage.mkdir(mode=0o755)
        stage.chmod(0o755)
        for name, data in files.items():
            target = stage / name
            target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            target.parent.chmod(0o755)
            write_new(target, data, 0o644)
        PACKAGE.rename(PREFIX / ('admin_executor.before-activation-' + digest))
        stage.rename(PACKAGE)
        for name, data in {'peers.json': {'web': web.pw_uid, 'backend': backend.pw_uid},
                           'desktop-identity.json': {'uid': desktop.pw_uid, 'gid': desktop.pw_gid},
                           'health.json': {'port': args.health_port}}.items():
            write_new(CONFIG / name, (json.dumps(data) + '\n').encode())
        write_new(CONFIG / 'return-url', (args.return_url + '\n').encode())
        templates = frozen(files, 'activation_units').units()
        for name, text in templates.items():
            write_new(UNITS / name, text.encode(), 0o644)
        run('/usr/bin/systemctl', 'daemon-reload')
        run('/usr/bin/systemctl', 'enable', '--now', *SERVICES)
        run('/usr/bin/systemctl', 'start', 'placet-admin-web.service')
        run('/usr/bin/systemctl', 'is-active', '--quiet', *SERVICES, 'placet-admin-web.service')
        checks.inspect_host(args.desktop_user)
        enabled = {'revision': 1, 'actions': [{'id': a, 'enabled': a != 'deploy'}
                   for a in ('deploy', 'restart-backend', 'restart-bridge')]}
        replace(CONFIG / 'catalogue.json', (json.dumps(enabled, indent=2) + '\n').encode())
        print('Two restart actions enabled. No restart job was authorized or executed. Tailscale is unchanged.')
    finally:
        os.close(lock)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--desktop-user', required=True)
    parser.add_argument('--return-url', required=True)
    parser.add_argument('--health-port', required=True, type=int)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--enable-restarts', action='store_true')
    parser.add_argument('--bundle-sha256')
    parser.add_argument('--installed-sha256')
    args = parser.parse_args(argv)
    checked_configuration(args)
    files, digest = snapshot(Path(__file__).resolve().parent)
    if not args.apply:
        installed = None
        try:
            installed = snapshot(PACKAGE)[1]
        except (FileNotFoundError, PermissionError):
            pass
        print(json.dumps({'mode': 'preview', 'bundle_sha256': digest, 'installed_sha256': installed,
                          'enable_requires': 'root, both reviewed pins, --enable-restarts and safe host preflight',
                          'actions': ['restart-backend', 'restart-bridge'], 'deploy': False}, indent=2))
        return
    if (os.geteuid() != 0 or args.bundle_sha256 != digest or not args.enable_restarts
            or not args.installed_sha256 or len(args.installed_sha256) != 64
            or any(c not in '0123456789abcdef' for c in args.installed_sha256)):
        parser.error('Root, exact source/installed digests and --enable-restarts are required')
    os.umask(0o077)
    apply(files, args)


if __name__ == '__main__':
    main()
