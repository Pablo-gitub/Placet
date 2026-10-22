"""Best-effort loading of existing Claude Desktop sessions, never message delivery.

The continue link is present in the installed app, but is not a public API.
"""
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid

from hooks.placet_common import active

LOCAL_ID = re.compile(r'local_[A-Za-z0-9-]{1,64}\Z')
COOLDOWN_SECONDS = 300


class ClaudeDesktopWake:
    def __init__(self, sessions_dir=None, state_dir=None, run=subprocess.run, clock=time.time):
        self.sessions_dir = Path(sessions_dir or Path.home() / '.config/Claude/claude-code-sessions')
        self.state_dir = Path(state_dir or Path.home() / '.cache/placet/claude-wake')
        self.run, self.clock = run, clock

    def resolve(self, session_id, cwd):
        matches = []
        for path in self.sessions_dir.glob('*/*/local_*.json'):
            if path.is_symlink():
                continue
            try:
                data = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if data.get('cliSessionId') != session_id:
                continue
            local_id = data.get('sessionId', '')
            if (not isinstance(local_id, str) or not LOCAL_ID.fullmatch(local_id)
                    or path.stem != local_id or data.get('isArchived')
                    or not cwd or data.get('cwd') != cwd):
                raise ValueError('Desktop session metadata does not match')
            matches.append(local_id)
        if len(matches) != 1:
            raise ValueError('Desktop session mapping missing or ambiguous')
        return matches[0]

    def open(self, session_id, cwd, force=False):
        session_id = str(uuid.UUID(session_id))
        if not cwd or not active(cwd):
            return False
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.state_dir.lstat()
        if self.state_dir.is_symlink() or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise PermissionError('Unsafe wake state directory')
        # Persist and lock the attempt before opening. A bridge restart or a
        # second process cannot repeatedly bring the same chat to the front.
        fd = os.open(self.state_dir / (session_id + '.attempt'),
                     os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'r+') as state:
            try:
                fcntl.flock(state, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            previous = state.read().strip()
            now = self.clock()
            # `force`: the user asked again; the cooldown protects against loops, not against them.
            if previous and not force and now - float(previous) < COOLDOWN_SECONDS:
                return False
            state.seek(0)
            state.truncate()
            state.write(str(now))
            state.flush()
            local_id = self.resolve(session_id, cwd)
            result = self.run(['/usr/bin/claude-desktop',
                               f'claude://code/continue?session={local_id}'],
                              capture_output=True, text=True, timeout=20)
            if result.returncode != 0:
                raise RuntimeError('Claude Desktop did not accept the conversation link')
        return True
