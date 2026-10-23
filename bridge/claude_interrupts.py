"""RFC-16, "cancelled in the app": whether a Claude Code conversation's last turn was stopped by the user.

Claude Code runs no hook when the user interrupts a turn (Esc): Stop does not fire, so Placet kept the
session busy and delivered nothing to it. The conversation file still records it, as a user message
whose whole text is the client's marker. Only the newest main-chain message counts: a turn that went on
afterwards (a new prompt, an answer, a tool result) was not left interrupted. Read-only, never raises.
"""
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from bridge.transcripts import _lines_backwards

MARKERS = ("[Request interrupted by user]", "[Request interrupted by user for tool use]")
MAX_BYTES = 512 * 1024


def _text_only(content) -> Optional[str]:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list) and len(content) == 1 and isinstance(content[0], dict) \
            and content[0].get("type") == "text":
        return str(content[0].get("text") or "").strip()
    return None


def last_turn_interrupted(path: Path) -> Optional[datetime]:
    """When the user interrupted the conversation's last turn, or None if it was not left interrupted."""
    try:
        with Path(path).open("rb") as f:
            end = f.seek(0, 2)
            for offset, line in _lines_backwards(f, end):
                if end - offset > MAX_BYTES:
                    return None
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(obj, dict) or obj.get("type") not in ("user", "assistant"):
                    continue
                if obj.get("isSidechain") or obj.get("isMeta"):
                    continue
                if obj["type"] == "assistant":
                    return None
                text = _text_only((obj.get("message") or {}).get("content"))
                if text not in MARKERS:
                    return None
                try:
                    return datetime.fromisoformat(str(obj.get("timestamp")).replace("Z", "+00:00"))
                except ValueError:
                    return None
    except OSError:
        return None
    return None
