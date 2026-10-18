"""Client of the opt-in, same-user desktop stdio adapter."""
import json
import os
from pathlib import Path
import socket
import stat


class DesktopClient:
    def __init__(self, path, allowed_threads=None):
        self.path = Path(path)
        self.allowed_threads = None if allowed_threads is None else set(allowed_threads)

    def enabled(self, session_id):
        return self.allowed_threads is None or session_id in self.allowed_threads

    def prepare(self, session_id):
        if not self.enabled(session_id):
            return False
        info = self.path.lstat()
        folder = self.path.parent.lstat()
        if (not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or not stat.S_ISDIR(folder.st_mode)
                or folder.st_uid != os.getuid() or folder.st_mode & 0o077):
            raise PermissionError('unsafe desktop adapter socket')
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(40)
            conn.connect(str(self.path))
            conn.sendall((json.dumps({'operation': 'resume', 'thread_id': session_id}) + '\n').encode())
            with conn.makefile('rb') as stream:
                reply = json.loads(stream.readline(65537))
        if reply.get('ok') is not True:
            raise RuntimeError('desktop adapter could not load conversation')
        return reply['result']['state'] == 'idle'
