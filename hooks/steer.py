#!/usr/bin/env python3
"""
"Subito" (RF-35): a message the user sends while a session is working is
put into the running turn after the agent's next action.

The bridge leaves it in ~/.cache/placet/steer/<session>.json (0600). This
PostToolUse hook takes it (an atomic rename: only one taker, the bridge
being the other), gives it to the model as additionalContext, and queues a
"steered" event so Placet knows it was delivered. Nothing to take: exit at
once, printing nothing. A `.placet-off` project is left alone.

The installer runs it behind a shell check, so Python starts only when a
message is waiting: `python3 steer.py --agent claude`.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import placet_common  # noqa: E402
import spool  # noqa: E402

ADAPTER = "steer.py/1"


def steer_dir() -> Path:
    override = os.environ.get("PLACET_STEER_DIR")
    return Path(override) if override else Path.home() / ".cache" / "placet" / "steer"


def take(session_id: str):
    """The waiting message for this session, taken so nobody else gets it."""
    path = steer_dir() / f"{session_id}.json"
    taken = path.with_name(f".{path.name}.taken-{os.getpid()}")
    try:
        os.rename(path, taken)
    except OSError:
        return None
    try:
        return json.loads(taken.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    finally:
        taken.unlink(missing_ok=True)


def main() -> None:
    agent = placet_common.agent_id(sys.argv)
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return
    session_id = event.get("session_id") or ""
    if not agent or not session_id or "/" in session_id or not placet_common.active(event.get("cwd")):
        return
    message = take(session_id)
    if not isinstance(message, dict) or not message.get("text"):
        return
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": event.get("hook_event_name") or "PostToolUse",
        "additionalContext": message["text"],
    }}))
    try:
        spool.write_event(agent, session_id, {
            "event": "steered", "request_id": message.get("request_id"),
            "provider": placet_common.option(sys.argv, "--provider") or "claude-code",
            "adapter": ADAPTER, "cwd": placet_common.project_root(event.get("cwd")),
        })
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    finally:
        sys.exit(0)
