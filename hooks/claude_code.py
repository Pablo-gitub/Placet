#!/usr/bin/env python3
"""
Claude Code hook for Placet (CLI and desktop app sessions): turns the
client's own events into facts Placet can rely on, instead of guessing from
the screen.

- SessionStart: registers the session (project, client, tmux session if any).
- UserPromptSubmit: a turn starts (busy). If the prompt carries a Placet
  request ("[Placet Request #N"), it is acknowledged here, without relying
  on the model calling placet_ack.
- PostToolUse on mcp__placet__*: links the request the session just created
  to it, so the outcome comes back to this very session.
- Stop: the turn ended (idle). If the turn was started by a Placet request
  and the model did not call placet_reply, its last message becomes an
  implicit reply.
- SessionEnd: the session is gone.

Usage: python3 claude_code.py --agent claude (or PLACET_HOOK_AGENT=claude).
A `.placet-off` file in the project keeps its sessions out of Placet.

It never prints anything (UserPromptSubmit stdout would be added to the
model's context) and always exits 0: a Placet problem must not block Claude.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import placet_common  # noqa: E402

PLACET_REQUEST = re.compile(r"\[Placet Request #(\d+)\b")
PLACET_TOOLS = re.compile(r"^mcp__placet__placet_(ask_human|send_to_agent)$")
MAX_MESSAGE_CHARS = 20000


def _tmux_session():
    if not os.environ.get("TMUX"):
        return None
    try:
        res = subprocess.run(["tmux", "display-message", "-p", "#S"],
                             capture_output=True, text=True, timeout=2)
        return res.stdout.strip() or None
    except Exception:
        return None


def _request_id_from_tool_response(response):
    """The id of the request a Placet MCP tool created, from its JSON result."""
    texts = []
    if isinstance(response, str):
        texts.append(response)
    elif isinstance(response, list):
        texts += [b.get("text", "") for b in response if isinstance(b, dict)]
    elif isinstance(response, dict):
        if "id" in response and "sender_id" in response:
            return int(response["id"])
        texts.append(json.dumps(response))
    for text in texts:
        try:
            data = json.loads(text)
        except (TypeError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("id"), int) and "sender_id" in data:
            return data["id"]
    return None


def build_event(event: dict):
    """The /api/sessions/{id}/events payload for a hook event, or None."""
    name = event.get("hook_event_name")
    common = {
        "provider": "claude-code",
        "entrypoint": os.environ.get("CLAUDE_CODE_ENTRYPOINT"),
        "cwd": event.get("cwd"),
        "title": event.get("session_title"),
    }
    if name == "SessionStart":
        return {"event": "start", "tmux_session": _tmux_session(), **common}
    if name == "UserPromptSubmit":
        payload = {"event": "prompt", **common}
        match = PLACET_REQUEST.search(event.get("prompt") or "")
        if match:
            payload["request_id"] = int(match.group(1))
        return payload
    if name == "PostToolUse" and PLACET_TOOLS.match(event.get("tool_name") or ""):
        request_id = _request_id_from_tool_response(event.get("tool_response"))
        return {"event": "tool", "request_id": request_id, **common} if request_id else None
    if name == "Stop":
        payload = {"event": "stop", **common}
        message = event.get("last_assistant_message")
        if message:
            payload["last_message"] = message[:MAX_MESSAGE_CHARS]
        return payload
    if name == "SessionEnd":
        return {"event": "end", **common}
    return None


def main() -> None:
    agent = placet_common.agent_id(sys.argv)
    if not agent:
        return
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return
    session_id = event.get("session_id")
    payload = build_event(event)
    token = placet_common.token_for(agent)
    if not session_id or payload is None or not token or placet_common.opted_out(event.get("cwd")):
        return
    payload = {k: v for k, v in payload.items() if v is not None}
    try:
        placet_common.call("POST", f"/api/sessions/{session_id}/events", token, payload)
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    finally:
        sys.exit(0)
