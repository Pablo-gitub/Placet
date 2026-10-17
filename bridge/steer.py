"""
The bridge's side of "Subito" (RF-35) for Claude Code: it leaves a "now"
message where the session's PostToolUse hook (hooks/steer.py) takes it, and
takes it back if the turn ends before another action. Both sides take the
file with an atomic rename, so a message is either inserted or re-queued,
never both.
"""
import json
import os
from pathlib import Path

from hooks.steer import steer_dir


def leave(session_id: str, request_id: int, text: str) -> Path:
    folder = steer_dir()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    final = folder / f"{session_id}.json"
    tmp = folder / f".{session_id}.json.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"request_id": request_id, "text": text}, f, ensure_ascii=False)
    os.replace(tmp, final)
    return final


def take_back(session_id: str, request_id: int) -> bool:
    """True if the message was still waiting (now removed): it never reached the turn."""
    path = steer_dir() / f"{session_id}.json"
    taken = path.with_name(f".{path.name}.back-{os.getpid()}")
    try:
        os.rename(path, taken)
    except OSError:
        return False
    try:
        data = json.loads(taken.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if data.get("request_id") != request_id:
        # Another message for the same session: put it back.
        os.replace(taken, path)
        return False
    taken.unlink(missing_ok=True)
    return True
