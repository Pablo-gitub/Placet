"""Bounded Unix RPC for public intents and cryptographic confirmation.

Peer identity comes from SO_PEERCRED, never JSON. Backend UID cannot call any
confirmation method; web UID cannot propose or dispatch an action. There is no
execution method, and the broker never invokes the supervisor.
"""
import json
import socket
import socketserver
import struct


LIMIT = 100000
METHODS = {
    'catalogue': (), 'propose': ('request_key', 'action', 'parameters'),
    'view': ('identifier',), 'challenge': ('identifier',),
    'authorize': ('identifier', 'challenge_id', 'assertion'),
}
ROLES = {'web': frozenset(('catalogue', 'view', 'challenge', 'authorize')),
         'backend': frozenset(('catalogue', 'propose', 'view'))}


class BrokerUnavailable(Exception):
    """Transport failure; contains no low-level error or secret."""


def dispatch(broker, *, peers, peer_uid, request, role=None):
    if (set(peers) != {'web', 'backend'} or any(type(uid) is not int or uid <= 0 for uid in peers.values())
            or peers['web'] == peers['backend']):
        raise ValueError('Distinct protected peer identities required')
    actual_role = next((name for name, uid in peers.items() if uid == peer_uid), None)
    if actual_role is None or (role is not None and role != actual_role):
        raise ValueError('Unknown administrative peer')
    if not isinstance(request, dict) or set(request) != {'method', 'params'}:
        raise ValueError('Invalid request envelope')
    method, params = request['method'], request['params']
    if (not isinstance(method, str) or method not in ROLES[actual_role]
            or not isinstance(params, dict) or set(params) != set(METHODS[method])):
        raise ValueError('Administrative method unavailable')
    return getattr(broker, method)(**params)


def peer_uid(connection):
    return struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[1]


class BrokerServer(socketserver.UnixStreamServer):
    """One bounded request per connection, no attacker-controlled log messages.

    The installer must create distinct protected directories/groups for the web
    and backend sockets. Role checking also applies after the socket is opened.
    Sequential requests bound resource use; timeouts limit incomplete clients.
    A permitted slow peer can delay the other socket by up to three seconds
    while sending its request; neither socket provides privileged dispatch.
    """
    allow_reuse_address = False

    def __init__(self, path, broker, *, peers, role):
        if role not in ROLES:
            raise ValueError('Invalid socket role')
        self.broker, self.peers, self.role = broker, peers, role
        super().__init__(str(path), _Handler)


class _Handler(socketserver.StreamRequestHandler):
    timeout = 3

    def handle(self):
        try:
            uid = peer_uid(self.request)
            if self.server.peers.get(self.server.role) != uid:
                raise ValueError('Wrong socket peer')
            raw = self.rfile.readline(LIMIT + 1)
            if len(raw) > LIMIT or not raw.endswith(b'\n'):
                raise ValueError('Request exceeds limit')
            result = dispatch(self.server.broker, peers=self.server.peers, peer_uid=uid,
                              request=json.loads(raw), role=self.server.role)
            response = {'ok': True, 'result': result}
        except Exception:
            response = {'ok': False, 'code': 'request_rejected'}
        encoded = json.dumps(response).encode() + b'\n'
        if len(encoded) > LIMIT:
            encoded = b'{"ok":false,"code":"request_rejected"}\n'
        try:
            self.wfile.write(encoded)
        except OSError:
            pass


class BrokerClient:
    def __init__(self, path, *, server_uid=0):
        self.path, self.server_uid = str(path), server_uid

    def call(self, method, **params):
        raw = json.dumps({'method': method, 'params': params}).encode() + b'\n'
        if len(raw) > LIMIT:
            raise ValueError('Request exceeds limit')
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(3)
                connection.connect(self.path)
                if peer_uid(connection) != self.server_uid:
                    raise BrokerUnavailable()
                connection.sendall(raw)
                with connection.makefile('rb') as stream:
                    response = stream.readline(LIMIT + 1)
            if len(response) > LIMIT or not response.endswith(b'\n'):
                raise BrokerUnavailable()
            envelope = json.loads(response)
            if envelope.get('ok') is not True:
                raise ValueError('Administrative request rejected')
            return envelope['result']
        except (OSError, json.JSONDecodeError, KeyError, TypeError, AttributeError) as error:
            raise BrokerUnavailable() from error


def main():
    """Fixed protected entry point; E provisions sockets, groups and units."""
    from contextlib import ExitStack
    import os
    from pathlib import Path
    import pwd
    import select
    import stat
    import sys
    from .authorization import AuthorizationStore
    from .broker import BrokerPolicy, ConfirmationBroker
    from .supervisor import protected_file, validate_directory, enabled_actions

    if os.geteuid() != 0:
        raise ValueError('The protected confirmation broker requires root')
    if len(sys.argv) != 1:
        raise ValueError('The broker accepts no arguments')
    module = Path('/usr/local/lib/placet-admin/admin_executor/control.py')
    if Path(__file__) != module:
        raise ValueError('Protected broker installation required')
    protected_file(module)
    config, state, runtime = Path('/etc/placet-admin'), Path('/var/lib/placet-admin'), Path('/run/placet-admin-control')
    validate_directory(state, private=True)
    for parent in (*state.parents, runtime, *runtime.parents):
        validate_directory(parent, private=False)
    database = state / 'authorization.sqlite'
    info = database.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise ValueError('Private authorization database required')
    peers = json.loads(protected_file(config / 'peers.json'))
    if (not isinstance(peers, dict) or set(peers) != {'web', 'backend'}
            or any(type(uid) is not int or uid <= 0 for uid in peers.values())
            or peers['web'] == peers['backend']):
        raise ValueError('Distinct protected peer identities required')
    origin = protected_file(config / 'origin').decode().strip()
    return_url = protected_file(config / 'return-url').decode().strip()
    os.umask(0o077)
    store = AuthorizationStore(database, rp_id=origin.removeprefix('https://'), origin=origin)
    def policy():
        raw = json.loads(protected_file(config / 'catalogue.json'))
        return BrokerPolicy(enabled=enabled_actions(raw), revision=raw['revision'])
    broker = ConfirmationBroker(store, policy=policy, return_url=return_url)
    broker.catalogue()  # Fail closed on unsupported configuration at startup.
    with ExitStack() as stack:
        servers = []
        for role, uid in peers.items():
            path = runtime / (role + '.sock')
            # No stale-socket deletion or reuse: the service manager owns the
            # lifetime of this root-controlled runtime directory.
            server = stack.enter_context(BrokerServer(path, broker, peers=peers, role=role))
            os.chown(path, 0, pwd.getpwuid(uid).pw_gid)
            path.chmod(0o660)
            servers.append(server)
        while True:
            readable, _, _ = select.select(servers, [], [])
            for server in readable:
                server.handle_request()


if __name__ == '__main__':
    main()
