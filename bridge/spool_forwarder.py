"""
Forwards the hooks' local event queue (hooks/spool.py) to the backend
(ARCHITETTURA 3.6).

The bridge is the only consumer (an flock on the folder). Events go in the
order they were observed; a file is deleted only once the backend answered
2xx, after storing it (the backend ignores an event_id it already applied).

- 401/403, 429, 5xx, network errors, missing token: retried with backoff;
  later events of the same session wait, so their order holds.
- Malformed or definitively refused events: moved to failed/, the others go on.
- Sessions of projects with `.placet-off`: dropped (RF-45).
- Temporary files left by a hook that died mid-write: removed after an hour.

New files are noticed through inotify, plus a full scan at start and every
SCAN_SECONDS (or polling, where inotify is unavailable).
"""
import ctypes
import ctypes.util
import fcntl
import json
import logging
import os
import select
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import httpx

from hooks import placet_common
from hooks.spool import FAILED, spool_dir

logger = logging.getLogger(__name__)

SCAN_SECONDS = 30.0
STALE_TMP_SECONDS = 3600.0
MAX_BACKOFF_SECONDS = 60.0
RETRY = "retry"

# send(agent, session_id, body) -> HTTP status, or RETRY when it cannot be sent now
Sender = Callable[[str, str, dict], object]


def http_sender(base_url: Optional[str] = None, client: Optional[httpx.Client] = None) -> Sender:
    client = client or httpx.Client(timeout=10.0)
    base = (base_url or placet_common.api_url()).rstrip("/")

    def send(agent: str, session_id: str, body: dict):
        token = placet_common.token_for(agent)
        if not token:
            return RETRY
        try:
            resp = client.post(f"{base}/api/sessions/{session_id}/events", json=body,
                               headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError:
            return RETRY
        return resp.status_code

    return send


def _retryable(status) -> bool:
    return status == RETRY or status in (401, 403, 408, 429) or (isinstance(status, int) and status >= 500)


class SpoolForwarder:
    def __init__(self, send: Sender, folder: Optional[Path] = None):
        self.send = send
        self.folder = folder or spool_dir()
        self.backoff = 0.0
        self._lock_fd = None

    # --- one pass ---------------------------------------------------------------------

    def _load(self) -> List[Tuple[tuple, Path, Optional[dict]]]:
        events = []
        now = time.time()
        for entry in os.scandir(self.folder):
            path = Path(entry.path)
            if not entry.is_file(follow_symlinks=False):
                continue
            if entry.name.startswith("."):
                if entry.name.endswith(".tmp") and now - entry.stat().st_mtime > STALE_TMP_SECONDS:
                    path.unlink(missing_ok=True)
                continue
            if not entry.name.endswith(".json"):
                continue
            try:
                event = json.loads(path.read_text(encoding="utf-8"))
                if not (isinstance(event, dict) and event.get("agent") and event.get("session_id")
                        and isinstance(event.get("body"), dict) and event.get("event_id")):
                    raise ValueError("missing fields")
                key = (float(event.get("at") or 0), int(event.get("seq") or 0), entry.name)
            except (OSError, ValueError, TypeError):
                event, key = None, (0.0, 0, entry.name)
            events.append((key, path, event))
        events.sort(key=lambda e: e[0])
        return events

    def _fail(self, path: Path, why: str) -> None:
        failed = self.folder / FAILED
        failed.mkdir(mode=0o700, exist_ok=True)
        logger.warning("Spool: %s moved to failed/ (%s)", path.name, why)
        os.replace(path, failed / path.name)

    def drain(self) -> Dict[str, int]:
        """Forwards what can be forwarded now; returns counts per outcome."""
        counts = {"sent": 0, "failed": 0, "dropped": 0, "waiting": 0}
        if not self.folder.is_dir():
            return counts
        blocked = set()
        for _, path, event in self._load():
            if event is None:
                self._fail(path, "malformed")
                counts["failed"] += 1
                continue
            session = event["session_id"]
            if session in blocked:
                counts["waiting"] += 1
                continue
            if not placet_common.active(event["body"].get("cwd")):
                path.unlink(missing_ok=True)
                counts["dropped"] += 1
                continue
            status = self.send(event["agent"], session, {**event["body"], "event_id": event["event_id"]})
            if isinstance(status, int) and 200 <= status < 300:
                path.unlink(missing_ok=True)
                counts["sent"] += 1
            elif _retryable(status):
                blocked.add(session)
                counts["waiting"] += 1
            else:
                self._fail(path, f"HTTP {status}")
                counts["failed"] += 1
        if counts["waiting"]:
            self.backoff = min(max(self.backoff * 2, 1.0), MAX_BACKOFF_SECONDS)
        else:
            self.backoff = 0.0
        return counts

    # --- running ----------------------------------------------------------------------

    def acquire(self) -> bool:
        """The single-consumer lock; False if another bridge holds it."""
        self.folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.folder / ".lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self._lock_fd = fd
        return True

    def run_forever(self) -> None:
        if not self.acquire():
            logger.error("Spool: another process is forwarding %s; not starting.", self.folder)
            return
        watch = _Inotify.open(self.folder)
        logger.info("Spool: forwarding %s (%s)", self.folder, "inotify" if watch else "polling")
        while True:
            try:
                self.drain()
            except Exception as e:
                logger.warning("Spool: %s", e)
                self.backoff = min(max(self.backoff * 2, 1.0), MAX_BACKOFF_SECONDS)
            if self.backoff:
                time.sleep(self.backoff)  # retry later, whatever arrives meanwhile
            elif watch:
                watch.wait(SCAN_SECONDS)
            else:
                time.sleep(1.0)


class _Inotify:
    """Minimal inotify through libc: wakes up when a file lands in the folder."""
    IN_CLOSE_WRITE = 0x00000008
    IN_MOVED_TO = 0x00000080
    IN_NONBLOCK = 0o4000
    IN_CLOEXEC = 0o2000000

    def __init__(self, fd: int):
        self.fd = fd

    @classmethod
    def open(cls, folder: Path) -> Optional["_Inotify"]:
        try:
            libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
            fd = libc.inotify_init1(cls.IN_NONBLOCK | cls.IN_CLOEXEC)
            if fd < 0:
                return None
            if libc.inotify_add_watch(fd, str(folder).encode(), cls.IN_MOVED_TO | cls.IN_CLOSE_WRITE) < 0:
                os.close(fd)
                return None
            return cls(fd)
        except (OSError, AttributeError):
            return None

    def wait(self, timeout: float) -> None:
        ready, _, _ = select.select([self.fd], [], [], timeout)
        if ready:
            time.sleep(0.05)  # let a burst of events of one turn land together
            try:
                while os.read(self.fd, 4096):
                    pass
            except BlockingIOError:
                pass


