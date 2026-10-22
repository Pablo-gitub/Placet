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
from bridge.claude_desktop_log import ClaudeDesktopLog

LOCAL_ID = re.compile(r'local_[A-Za-z0-9-]{1,64}\Z')
COOLDOWN_SECONDS = 300
# archived_states(): how many records and how big a record are worth reading.
MAX_RECORDS = 2000
MAX_RECORD_BYTES = 1_000_000


class ClaudeDesktopWake:
    def __init__(self, sessions_dir=None, state_dir=None, run=subprocess.run, clock=time.time, log=None):
        self.sessions_dir = Path(sessions_dir or Path.home() / '.config/Claude/claude-code-sessions')
        self.state_dir = Path(state_dir or Path.home() / '.cache/placet/claude-wake')
        self.run, self.clock = run, clock
        self.log = log or ClaudeDesktopLog()

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

    def _records(self):
        """(local id, Placet session id, archived) of each Desktop record that can be read safely, as resolve() reads."""
        try:
            paths = sorted(self.sessions_dir.glob('*/*/local_*.json'))[:MAX_RECORDS]
        except OSError:
            return
        for path in paths:
            try:
                if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_RECORD_BYTES:
                    continue
                data = json.loads(path.read_text())
                cli_id = str(uuid.UUID(data.get('cliSessionId')))
                archived = bool(data.get('isArchived'))
            except (OSError, ValueError, TypeError, AttributeError):
                continue
            yield path.stem, cli_id, archived

    def archived_states(self):
        """
        {Placet session id: archived in the app?} for the conversations whose Desktop record can be
        read: the same safety as resolve() (no symlinks, plain files, bounded size and count). A
        session with several records, an unreadable one or an odd id is left out: no claim.
        """
        seen, ambiguous = {}, set()
        for _local, cli_id, archived in self._records():
            if cli_id in seen:
                ambiguous.add(cli_id)
            seen[cli_id] = archived
        return {k: v for k, v in seen.items() if k not in ambiguous}

    def local_to_cli(self):
        """{local_<uuid>: Placet session id}, read with the same safety as archived_states()."""
        return {local: cli_id for local, cli_id, _archived in self._records() if LOCAL_ID.fullmatch(local)}

    def diagnose(self, session_id, cwd, since):
        """Why the app did not start the session this attempt asked for (a closed code), if its log says so."""
        try:
            local_id = self.resolve(str(uuid.UUID(session_id)), cwd)
            return self.log.diagnose(local_id, since)
        except Exception:
            return None
