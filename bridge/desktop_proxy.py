"""Opt-in stdio proxy for the *desktop's own* Codex app-server.

Used as CODEX_CLI_PATH. No second initialize, model turn, or execution engine.
The private local API exposes only status and resume, never arbitrary RPC.
"""
import asyncio
import fcntl
import json
import os
from pathlib import Path
import socket
import stat
import struct
import sys
import uuid

MAX_LINE = 1024 * 1024


class DesktopProxy:
    def __init__(self, child, output):
        self.child, self.output = child, output
        self.pending = {}
        self.ready = asyncio.Event()
        self.closed = False
        self.prefix = 'placet-' + uuid.uuid4().hex + '-'
        self.sequence = 0
        self.write_lock = asyncio.Lock()
        self.resume_lock = asyncio.Lock()
        self.initialize_id = None

    async def write(self, raw):
        async with self.write_lock:
            self.child.stdin.write(raw)
            await self.child.stdin.drain()

    async def desktop_input(self, source):
        while raw := await source.readline():
            # Forward the desktop's handshake unchanged. Never initialize an
            # additional logical client on its connection.
            try:
                message = json.loads(raw)
                if message.get('method') == 'initialize':
                    self.initialize_id = message.get('id')
                if message.get('method') == 'initialized':
                    self.ready.set()
            except (ValueError, AttributeError):
                pass
            await self.write(raw)

    async def server_output(self):
        try:
            while raw := await self.child.stdout.readline():
                try:
                    message = json.loads(raw)
                    key = message.get('id')
                    if (self.initialize_id is not None and key == self.initialize_id
                            and 'result' in message and 'error' not in message):
                        self.ready.set()
                    ours = isinstance(key, str) and key.startswith(self.prefix)
                    future = self.pending.get(key) if ours else None
                except (ValueError, AttributeError):
                    ours = False
                    future = None
                if ours:
                    if future is not None and not future.done():
                        future.set_result(message)
                else:
                    # All notifications and desktop replies keep reaching the
                    # app, including approval and dynamic-tool requests.
                    self.output(raw)
        finally:
            self.closed = True
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(ConnectionError('desktop app-server exited'))

    async def rpc(self, method, params):
        if not self.ready.is_set() or self.closed:
            raise ConnectionError('desktop connection is not initialized')
        self.sequence += 1
        key = f'{self.prefix}{self.sequence}'
        future = asyncio.get_running_loop().create_future()
        self.pending[key] = future
        try:
            await self.write((json.dumps({'id': key, 'method': method, 'params': params}) + '\n').encode())
            reply = await asyncio.wait_for(future, 15)
            if 'error' in reply:
                raise RuntimeError('app-server rejected ' + method)
            return reply['result']
        finally:
            # server_output also consumes late replies with our reserved prefix.
            self.pending.pop(key, None)

    async def status(self, thread_id):
        result = await self.rpc('thread/read', {'threadId': thread_id, 'includeTurns': False})
        value = result.get('thread', {}).get('status', {})
        value = value.get('type') if isinstance(value, dict) else value
        if value not in ('idle', 'active', 'notLoaded', 'systemError'):
            raise RuntimeError('unsupported thread status')
        return {'state': value}

    async def operation(self, request):
        if request == {'operation': 'health'}:
            return {'protocol': 1, 'connected': self.ready.is_set() and not self.closed}
        if set(request) != {'operation', 'thread_id'}:
            raise ValueError('invalid request')
        thread_id = str(uuid.UUID(request['thread_id']))
        if request['operation'] == 'status':
            return await self.status(thread_id)
        if request['operation'] == 'resume':
            async with self.resume_lock:
                state = await self.status(thread_id)
                if state['state'] == 'notLoaded':
                    await self.rpc('thread/resume', {'threadId': thread_id})
                    state = await self.status(thread_id)
                return state
        raise ValueError('unsupported operation')

    async def client(self, reader, writer):
        try:
            peer = writer.get_extra_info('socket')
            _, uid, _ = struct.unpack('3i', peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid != os.getuid():
                raise PermissionError('wrong user')
            raw = await asyncio.wait_for(reader.readline(), 5)
            if len(raw) > MAX_LINE:
                raise ValueError('request too large')
            result = await self.operation(json.loads(raw))
            reply = {'ok': True, 'result': result}
        except Exception as exc:
            # No app-server payloads, prompts, paths or credentials in errors.
            reply = {'ok': False, 'error': type(exc).__name__}
        try:
            writer.write((json.dumps(reply) + '\n').encode())
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()


def private_directory(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise PermissionError('adapter directory must be owned by this user with mode 0700')


async def serve(binary, args, path):
    private_directory(path.parent)
    # Single owner. A second desktop instance must not delete a live socket.
    lock = (path.parent / 'proxy.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if path.exists() or path.is_symlink():
        if not stat.S_ISSOCK(path.lstat().st_mode):
            raise PermissionError('adapter path is not a socket')
        path.unlink()
    child = await asyncio.create_subprocess_exec(binary, *args, stdin=asyncio.subprocess.PIPE,
                                                 stdout=asyncio.subprocess.PIPE, limit=64 * 1024 * 1024)
    proxy = DesktopProxy(child, lambda raw: sys.stdout.buffer.write(raw) or None)
    # Flush every protocol message; stdout belongs exclusively to the app.
    def output(raw):
        sys.stdout.buffer.write(raw)
        sys.stdout.buffer.flush()
    proxy.output = output
    source = asyncio.StreamReader(limit=64 * 1024 * 1024)
    protocol = asyncio.StreamReaderProtocol(source)
    transport, _ = await asyncio.get_running_loop().connect_read_pipe(lambda: protocol, sys.stdin.buffer)
    tasks = []
    server = None
    try:
        server = await asyncio.start_unix_server(proxy.client, path=path, limit=MAX_LINE)
        os.chmod(path, 0o600)
        tasks = [asyncio.create_task(proxy.desktop_input(source)), asyncio.create_task(proxy.server_output())]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        if server:
            server.close()
            await server.wait_closed()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        transport.close()
        if child.returncode is None:
            child.terminate()
            try:
                await asyncio.wait_for(child.wait(), 5)
            except asyncio.TimeoutError:
                child.kill()
                await child.wait()
        path.unlink(missing_ok=True)
        lock.close()


def main():
    binary = os.environ.get('PLACET_CODEX_REAL_BIN', '/usr/lib/chatgpt/resources/codex')
    args = sys.argv[1:]
    # Versions, help, queue and every other command retain native behavior.
    if 'app-server' not in args or any(x in args for x in ('--help', '-h', 'generate-ts', 'generate-json-schema', 'daemon', 'proxy')):
        os.execv(binary, [binary, *args])
    path = Path(os.environ.get('PLACET_DESKTOP_SOCKET', str(Path.home() / '.cache/placet/desktop/codex.sock')))
    asyncio.run(serve(binary, args, path))


if __name__ == '__main__':
    main()
