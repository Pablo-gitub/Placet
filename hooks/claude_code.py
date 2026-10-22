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
Codex hooks receive the same fields (session_id, transcript_path, prompt,
last_assistant_message, tool_name, ...): the same script serves them with
`--agent codex --provider codex`.
A `.placet-off` file in a project (or a folder containing it) keeps its sessions out of Placet.

Events are written to the local queue (spool.py), which the bridge forwards
to the backend: the hook makes no network call and returns at once.

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
import spool  # noqa: E402

PLACET_REQUEST = re.compile(r"\[Placet Request #(\d+)\b")
# RF-29d: continued conversation prompt from Claude Code (transcript ending in <uuid>.jsonl)
CONTINUED_PREFIX = "This session is being continued from a previous conversation"
CONTINUED_JSONL_UUID = re.compile(
    r"\b([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\.jsonl\b"
)
# Claude Code names MCP tools mcp__<server>__<tool>; other clients may not.
PLACET_TOOLS = re.compile(r"(^|__|\.|/)placet_(ask_human|send_to_agent)$")
MAX_MESSAGE_CHARS = 20000
ADAPTER = "claude_code.py/2"


def _tmux_session():
    if not os.environ.get("TMUX"):
        return None
    try:
        res = subprocess.run(["tmux", "display-message", "-p", "#S"],
                             capture_output=True, text=True, timeout=2)
        return res.stdout.strip() or None
    except Exception:
        return None


def _request_id_from_tool_response(response, depth: int = 0):
    """
    The id of the request a Placet MCP tool created. The tool returns the
    request as JSON text; clients wrap it differently (plain string, content
    blocks, {"content": [...]}, structured result), so search recursively.
    """
    if depth > 6 or response is None:
        return None
    if isinstance(response, dict):
        if isinstance(response.get("id"), int) and "sender_id" in response:
            return response["id"]
        children = list(response.values())
    elif isinstance(response, list):
        children = response
    elif isinstance(response, str):
        try:
            return _request_id_from_tool_response(json.loads(response), depth + 1)
        except (TypeError, ValueError):
            return None
    else:
        return None
    for child in children:
        found = _request_id_from_tool_response(child, depth + 1)
        if found is not None:
            return found
    return None


def build_event(event: dict, provider: str = "claude-code"):
    """The /api/sessions/{id}/events payload for a hook event, or None."""
    name = event.get("hook_event_name")
    common = {
        "provider": provider,
        "entrypoint": os.environ.get("CLAUDE_CODE_ENTRYPOINT") if provider == "claude-code" else provider,
        "cwd": event.get("cwd"),
        "project_root": placet_common.project_root(event.get("cwd")),
        "adapter": ADAPTER,
        "title": event.get("session_title"),
        # On every event: a session may predate its first SessionStart hook.
        "tmux_session": _tmux_session(),
    }
    if name == "SessionStart":
        # "clear" lets Placet move the node to the new conversation (RFC-02).
        return {"event": "start", "start_source": event.get("source"), **common}
    if name == "UserPromptSubmit":
        payload = {"event": "prompt", **common}
        prompt_text = event.get("prompt") or ""
        match = PLACET_REQUEST.search(prompt_text)
        if match:
            payload["request_id"] = int(match.group(1))
        cont_idx = prompt_text.find(CONTINUED_PREFIX)
        if cont_idx != -1:
            jsonl_uuids = CONTINUED_JSONL_UUID.findall(prompt_text[cont_idx:])
            if jsonl_uuids:
                payload["continues"] = jsonl_uuids[-1].lower()
        return payload
    if name == "PostToolUse" and PLACET_TOOLS.search(event.get("tool_name") or ""):
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
    payload = build_event(event, provider=placet_common.option(sys.argv, "--provider") or "claude-code")
    if not session_id or payload is None or not placet_common.active(event.get("cwd")):
        return
    try:
        spool.write_event(agent, session_id, {k: v for k, v in payload.items() if v is not None})
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    finally:
        sys.exit(0)
