#!/usr/bin/env python3
"""
Claude Code hook for Placet: turns the CLI's own events into facts Placet
can rely on, instead of guessing from the screen.

- UserPromptSubmit: the agent started a turn (busy). If the prompt is a
  Placet injection ("[Placet Request #N ...") it is acknowledged here, so the
  model does not have to remember to call placet_ack.
- Stop: the turn ended (idle). If the turn was started by a Placet request
  and the model did not call placet_reply, its last message is recorded as
  an implicit reply.

The hook acts only in sessions started with PLACET_HOOK_AGENT=<agent id>
(e.g. `tmux new -s agent-claude -e PLACET_HOOK_AGENT=claude claude`), so it
can sit in the global settings without touching your own sessions.

It never prints anything (UserPromptSubmit stdout would be added to the
model's context) and always exits 0: a Placet problem must not block the CLI.

Configuration (environment):
  PLACET_HOOK_AGENT   agent id from AGENTS.md (required to act)
  PLACET_API_URL      backend address (default http://127.0.0.1:8000)
  PLACET_API_TOKEN    the agent's token; otherwise PLACET_TOKEN_<AGENT> from
                      ~/.config/placet/agent-tokens.env
"""
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

PLACET_REQUEST = re.compile(r"\[Placet Request #(\d+)\b")
TIMEOUT_SECONDS = 3
MAX_MESSAGE_CHARS = 20000


def _token(agent_id: str) -> str:
    token = os.environ.get("PLACET_API_TOKEN", "")
    if token:
        return token
    tokens_file = Path.home() / ".config" / "placet" / "agent-tokens.env"
    key = f"PLACET_TOKEN_{agent_id.upper()}="
    try:
        for line in tokens_file.read_text().splitlines():
            if line.startswith(key):
                return line[len(key):].strip()
    except OSError:
        pass
    return ""


def build_state(event: dict) -> dict | None:
    """The /api/agents/me/state payload for a hook event, or None to ignore it."""
    name = event.get("hook_event_name")
    if name == "UserPromptSubmit":
        payload = {"state": "busy"}
        match = PLACET_REQUEST.search(event.get("prompt") or "")
        if match:
            payload["request_id"] = int(match.group(1))
        return payload
    if name == "Stop":
        payload = {"state": "idle"}
        message = event.get("last_assistant_message")
        if message:
            payload["last_message"] = message[:MAX_MESSAGE_CHARS]
        return payload
    return None


def main() -> None:
    agent_id = os.environ.get("PLACET_HOOK_AGENT", "")
    if not agent_id:
        return
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return
    payload = build_state(event)
    token = _token(agent_id)
    if payload is None or not token:
        return
    base_url = os.environ.get("PLACET_API_URL", "http://127.0.0.1:8000").rstrip("/")
    request = urllib.request.Request(
        f"{base_url}/api/agents/me/state",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    try:
        urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS).close()
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    finally:
        sys.exit(0)
