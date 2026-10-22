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
# RF-29d fallback: the transcript of a continued session starts with service records
# (title, mode, compact boundary, file-history snapshots of up to hundreds of KB)
# and only then the summary, as the first main-conversation user record. It is read
# line by line up to that record, with a record and a byte budget.
TRANSCRIPT_MAX_RECORDS = 64
TRANSCRIPT_MAX_BYTES = 1024 * 1024
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


def _summary_text(record):
    """
    The text of a continuation summary record, or None for anything else. The
    structure Claude Code writes (observed, see bridge/transcripts.py): a
    `user` record from the main conversation whose message is the user's own
    text, as a string or text blocks. Assistant, tool, subagent and metadata
    records never count.
    """
    if record.get("type") != "user" or record.get("isSidechain") or record.get("isMeta"):
        return None
    message = record.get("message")
    if not isinstance(message, dict) or message.get("role") not in (None, "user"):
        return None
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list) and content and all(
            isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str) for b in content):
        return "\n".join(b["text"] for b in content)
    return None


def continued_from_transcript(path):
    """
    The session this one continues, from the head of its own transcript: the
    first main-conversation record is a user record whose text starts with
    Claude Code's continuation phrase and names the previous transcript.
    Service records before it (title, mode, system, file-history snapshots...)
    and a subagent's records are skipped; the first user or assistant record of
    the main conversation decides. Only that explicit reference counts: an
    assistant message, a tool result, a later or pasted message, a summary naming
    several transcripts, or the transcript itself is not proof. The read is
    bounded (records and bytes); the backend decides whether it is the first
    prompt. None when the file is unreadable or says nothing.
    """
    if not path:
        return None
    budget = TRANSCRIPT_MAX_BYTES
    try:
        with open(path, "rb") as handle:
            for _ in range(TRANSCRIPT_MAX_RECORDS):
                raw = handle.readline(budget + 1)
                if not raw or len(raw) > budget:
                    return None  # end of file, or the byte budget ran out
                budget -= len(raw)
                try:
                    record = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(record, dict) or record.get("isSidechain") or record.get("isMeta"):
                    continue
                kind = record.get("type")
                if kind == "assistant":
                    return None
                if kind != "user":
                    continue  # a service record
                # The first user record of the conversation decides, whatever it says.
                text = _summary_text(record)
                if record.get("isCompactSummary") is False or text is None \
                        or not text.lstrip().startswith(CONTINUED_PREFIX):
                    return None
                own = Path(path).stem.lower()
                found = {u.lower() for u in CONTINUED_JSONL_UUID.findall(text)} - {own}
                return found.pop() if len(found) == 1 else None
    except OSError:
        return None
    return None


def build_event(event: dict, provider: str = "claude-code"):
    """The /api/sessions/{id}/events payload for a hook event, or None."""
    name = event.get("hook_event_name")
    common = {
        "provider": provider,
        "entrypoint": os.environ.get("CLAUDE_CODE_ENTRYPOINT") if provider == "claude-code" else provider,
        "cwd": event.get("cwd"),
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
        if "continues" not in payload and provider == "claude-code":
            # The summary may not reach the prompt hook: the transcript has it.
            found = continued_from_transcript(event.get("transcript_path"))
            if found:
                payload["continues"] = found
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
