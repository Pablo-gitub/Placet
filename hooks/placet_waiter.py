#!/usr/bin/env python3
"""
Background waiter for a Claude Code session (hook with "async": true and
"asyncRewake": true on SessionStart and Stop).

It long-polls the session's Placet inbox. When a message arrives (an outcome
of a request the session made, or a prompt from another agent) it prints the
text on stderr and exits 2: Claude Code then wakes the session with that text.

One waiter per session (a pid lock); it exits when the session ends, when
Claude Code is gone, or after a day.
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import placet_common  # noqa: E402

LOCK_DIR = Path.home() / ".cache" / "placet" / "waiters"
POLL_SECONDS = 240
MAX_LIFETIME_SECONDS = 24 * 3600


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


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
    lock = LOCK_DIR / f"{session_id}.pid"
    try:
        if _alive(int(lock.read_text())):
            return None  # another waiter is listening for this session
    except (OSError, ValueError):
        pass
    lock.write_text(str(os.getpid()))
    return lock


def main() -> int:
    agent = placet_common.agent_id(sys.argv)
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    session_id = event.get("session_id")
    token = placet_common.token_for(agent) if agent else ""
    if not session_id or not token or not placet_common.active(event.get("cwd")):
        return 0
    lock = _acquire(session_id)
    if lock is None:
        return 0
    # The hook may run through a short-lived shell: Claude Code is gone only
    # when none of the original ancestors is alive any more.
    ancestors = _ancestors()

    def client_alive() -> bool:
        return not ancestors or any(_alive(pid) for pid in ancestors)

    started = time.time()
    backoff = 5
    try:
        while time.time() - started < MAX_LIFETIME_SECONDS and client_alive():
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
                return 0  # unknown or ended session, or bad token: stop
            if status != 204:
                time.sleep(5)
        return 0
    finally:
        try:
            if lock.read_text() == str(os.getpid()):
                lock.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
