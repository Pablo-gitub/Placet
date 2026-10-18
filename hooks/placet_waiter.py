#!/usr/bin/env python3
"""
Background waiter for a Claude Code session (hook with "async": true and
"asyncRewake": true on SessionStart and Stop).

It long-polls the session's Placet inbox. When a message arrives (an outcome
of a request the session made, or a prompt from another agent) it prints the
text on stderr and exits 2: Claude Code then wakes the session with that text.

One waiter per session (a pid lock); it exits when the session ends, when
Claude Code is gone. The owning hook has an explicit seven-day timeout.
"""
import json
import fcntl
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import placet_common  # noqa: E402

LOCK_DIR = Path.home() / ".cache" / "placet" / "waiters"
POLL_SECONDS = 240
logger = logging.getLogger("placet.waiter")


def _ancestors() -> list:
    """Pids above this process (Linux /proc), without init."""
    pids, pid = [], os.getppid()
    while pid > 1 and len(pids) < 16:
        pids.append(pid)
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
            pid = int(stat.rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
    return pids


def _acquire(session_id: str):
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    # Keep the inode: unlinking a locked file lets a new caller lock a different
    # inode while another waiter still owns the old one.
    lock = (LOCK_DIR / f"{session_id}.pid").open("a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return None
    lock.seek(0)
    lock.truncate()
    lock.write(str(os.getpid()))
    lock.flush()
    return lock


def _owner():
    """Pin the nearest Claude engine, not its long-lived desktop or shell."""
    for pid in _ancestors():
        try:
            proc = Path(f"/proc/{pid}")
            executable = (proc / "exe").resolve()
            # Standalone installations execute a version-named native file;
            # Desktop's bundled engine is named simply 'claude'.
            if (executable.name == "claude" or (proc / "comm").read_text().strip() == "claude"):
                return pid, (proc / "stat").read_text().rsplit(")", 1)[1].split()[19]
        except (OSError, IndexError):
            continue
    return None


def _owner_alive(owner):
    if owner is None:
        return False
    pid, started = owner
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return fields[0] != "Z" and fields[19] == started
    except (OSError, IndexError):
        return False


def main() -> int:
    agent = placet_common.agent_id(sys.argv)
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    session_id = event.get("session_id")
    token = placet_common.token_for(agent) if agent else ""
    if (not isinstance(session_id, str) or not session_id or "/" in session_id
            or session_id in (".", "..") or not token or not placet_common.active(event.get("cwd"))):
        return 0
    lock = _acquire(session_id)
    if lock is None:
        return 0
    owner = _owner()
    backoff = 5
    try:
        while _owner_alive(owner) and placet_common.active(event.get("cwd")):
            try:
                status, body = placet_common.call(
                    "GET", f"/api/sessions/{session_id}/inbox?wait_seconds={POLL_SECONDS}",
                    token, timeout=POLL_SECONDS + 15,
                )
            except Exception:
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)
                continue
            backoff = 5
            if status == 200 and body and body.get("text"):
                print(body["text"], file=sys.stderr)
                return 2
            if status in (401, 403, 404, 410):
                logger.warning("Waiter for %s stopped: inbox HTTP %s", session_id, status)
                return 0  # unknown or ended session, or bad token: stop
            if status != 204:
                time.sleep(5)
        logger.warning("Waiter for %s stopped: engine gone or project excluded", session_id)
        return 0
    finally:
        lock.seek(0)
        lock.truncate()
        lock.close()


if __name__ == "__main__":
    sys.exit(main())
