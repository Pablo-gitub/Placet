"""
Reads the conversation of an agent session from the provider's own files,
for the PWA (RF-30, RF-32).

Runs in the bridge, as the user who owns the sessions: the backend never
reads these files. The file is located from the provider and the session
id only, under the provider's own folder (RS-06); no path ever comes from
the backend.

All supported providers write JSON Lines, so a page is a byte range of the
file: the cursor is the offset of a line. Reading goes backwards from the end
(`before`) for older messages, forwards (`after`) for new ones.

What is shown: the user's messages and the agent's text answers; each tool
call becomes one `action` entry; tool results and internal reasoning are
never returned.
"""
import json
import os
import re
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Tuple

SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
DEFAULT_LIMIT = 40
MAX_LIMIT = 200
MAX_TEXT_CHARS = 20000
MAX_DETAIL_CHARS = 300
BLOCK_BYTES = 64 * 1024
# Forward reads (live updates) stop after this much: the client asks again.
MAX_FORWARD_BYTES = 4 * 1024 * 1024


from bridge import attachments as att  # noqa: E402


def _with_attachments(entries: List[dict], found: List[dict], at: Optional[str], role: str = "user") -> List[dict]:
    """Puts the images of a message on its entry (a new one if it had no text)."""
    if not found:
        return entries
    for entry in entries:
        if entry["role"] in ("user", "placet", "assistant"):
            entry.setdefault("attachments", []).extend(found)
            return entries
    return entries + [_entry(role, "", at, attachments=list(found))]


class TranscriptError(Exception):
    """`code` is one of: invalid, unsupported, not_found."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _clip(text: str, limit: int = MAX_TEXT_CHARS) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _entry(role: str, text: str = "", at: Optional[str] = None, **extra) -> dict:
    entry = {"role": role, "text": text, "at": at}
    entry.update({k: v for k, v in extra.items() if v is not None})
    return entry


def _action(name: str, kind: str, detail: Optional[str], at: Optional[str]) -> dict:
    detail = _clip(" ".join(str(detail).split()), MAX_DETAIL_CHARS) if detail else None
    return _entry("action", at=at, tool=name, kind=kind, detail=detail)


# A message the user wrote from the PWA (RF-34), as delivered by Placet:
# "[Placet Request #N dall'utente]: <text>" plus a note for the agent.
_USER_MESSAGE = re.compile(r"^\s*\[Placet Request #\d+ dall'utente\]:\s*(.*?)(?:\nÈ un messaggio che l'utente ti scrive.*)?$", re.S)


_REQUEST_ID = re.compile(r"^\s*\[Placet Request #(\d+)\b")


def _placet_or_user(text: str, at: Optional[str]) -> dict:
    """A user turn; one Placet delivered carries its request_id (the proof of delivery)."""
    found = _REQUEST_ID.match(text)
    request_id = int(found.group(1)) if found else None
    mine = _USER_MESSAGE.match(text)
    if mine:
        return _entry("user", _clip(mine.group(1)), at, via="placet", request_id=request_id)
    return _entry("placet" if found or text.lstrip().startswith("[Placet") else "user", _clip(text), at,
                  request_id=request_id)


# --- Claude Code ----------------------------------------------------------------

_CLAUDE_TOOLS = {
    "Bash": ("command", "command"),
    "Read": ("read", "file_path"),
    "Edit": ("edit", "file_path"),
    "MultiEdit": ("edit", "file_path"),
    "Write": ("write", "file_path"),
    "NotebookEdit": ("edit", "notebook_path"),
    "Grep": ("search", "pattern"),
    "Glob": ("search", "pattern"),
    "WebFetch": ("web", "url"),
    "WebSearch": ("web", "query"),
    "Skill": ("skill", "skill"),
    "Task": ("agent", "description"),
    "Agent": ("agent", "description"),
    "ToolSearch": ("tools", "query"),
    "TodoWrite": ("plan", None),
}
_STOP_HOOK = re.compile(r'blocking error from command "[^"]*":\s*(.*?)\s*(?:</system-reminder>|$)', re.S)


def _claude_user_text(text: str, at: Optional[str]) -> List[dict]:
    stripped = text.lstrip()
    if stripped.startswith("<task-notification>"):
        # A Placet wake-up delivered by the background waiter (asyncRewake).
        match = _STOP_HOOK.search(text)
        return [_placet_or_user(match.group(1), at)] if match else []
    if stripped.startswith("<command-name>"):
        name = re.search(r"<command-name>(.*?)</command-name>", text, re.S)
        args = re.search(r"<command-args>(.*?)</command-args>", text, re.S)
        command = " ".join(p.strip() for p in (name and name.group(1), args and args.group(1)) if p and p.strip())
        return [_entry("command", command, at)] if command else []
    if stripped.startswith(("<local-command-", "<system-reminder>", "<bash-")):
        return []
    return [_placet_or_user(text, at)] if stripped else []


def parse_claude(obj: dict) -> List[dict]:
    if obj.get("isSidechain"):
        return []  # a subagent's own conversation
    kind, at = obj.get("type"), obj.get("timestamp")
    if kind == "system":
        return [_entry("compact", at=at)] if obj.get("subtype") == "compact_boundary" else []
    message = obj.get("message") or {}
    content = message.get("content")
    if kind == "user":
        if obj.get("isMeta") or obj.get("isCompactSummary"):
            return []
        if isinstance(content, str):
            return _claude_user_text(content, at)
        if isinstance(content, list):
            parts, images = [], []
            for block in content:
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "image":
                    source = block.get("source") or {}
                    if source.get("type") == "base64":
                        images.append(att.inline(source.get("media_type"), source.get("data")))
            text = "\n".join(p for p in parts if p.strip())
            return _with_attachments(_claude_user_text(text, at) if text else [], images, at)
        return []
    if kind == "assistant" and isinstance(content, list):
        if obj.get("isApiErrorMessage"):
            texts = [b.get("text", "") for b in content if b.get("type") == "text"]
            return [_entry("notice", _clip(" ".join(texts)), at)]
        out = []
        for block in content:
            if block.get("type") == "text" and block.get("text", "").strip():
                out.append(_entry("assistant", _clip(block["text"]), at))
            elif block.get("type") == "tool_use":
                name = block.get("name", "")
                args = block.get("input") or {}
                if name.startswith("mcp__"):
                    _, server, tool = (name.split("__", 2) + ["", ""])[:3]
                    out.append(_action(tool or name, "mcp", server, at))
                    continue
                tool_kind, key = _CLAUDE_TOOLS.get(name, ("other", None))
                out.append(_action(name, tool_kind, args.get(key) if key else None, at))
        return out
    return []


# --- Codex ----------------------------------------------------------------------


def _codex_text(content) -> str:
    return "\n".join(
        part.get("text", "") for part in content or [] if isinstance(part, dict)
    )


# Codex lists the files the user attached before the request itself.
_CODEX_FILES_PREAMBLE = re.compile(r"^\s*# Files mentioned by the user:.*?## My request:\s*", re.S)


def parse_codex(obj: dict) -> List[dict]:
    at = obj.get("timestamp")
    if obj.get("type") == "compacted":
        return [_entry("compact", at=at)]
    payload = obj.get("payload") or {}
    if obj.get("type") != "event_msg" or payload.get("type") != "item_completed":
        return []
    item = payload.get("item") or {}
    kind = item.get("type")
    if kind == "UserMessage":
        text = _CODEX_FILES_PREAMBLE.sub("", _codex_text(item.get("content")))
        images = [
            att.file(part["path"]) for part in item.get("content") or []
            if isinstance(part, dict) and part.get("type") == "local_image" and part.get("path")
        ]
        return _with_attachments([_placet_or_user(text, at)] if text.strip() else [], images, at)
    if kind == "AgentMessage":
        text = _codex_text(item.get("content"))
        return [_entry("assistant", _clip(text), at)] if text.strip() else []
    if kind == "CommandExecution":
        command = item.get("command")
        if isinstance(command, list):
            # ["/bin/bash", "-lc", "<script>"]: show the script.
            command = command[-1] if command else ""
        return [_action("exec", "command", command, at)]
    if kind == "McpToolCall":
        return [_action(item.get("tool", ""), "mcp", item.get("server"), at)]
    if kind == "Extension":
        action = item.get("action") or {}
        queries = action.get("queries") or [item.get("query") or action.get("query")]
        return [_action(item.get("kind", "extension"), "web", " · ".join(q for q in queries if q), at)]
    if kind == "FileChange":
        paths = [c.get("path") for c in item.get("changes") or [] if isinstance(c, dict)]
        return [_action("apply_patch", "edit", ", ".join(p for p in paths if p), at)]
    if kind == "ContextCompaction":
        return []  # the `compacted` line already marks it
    if kind in ("Reasoning",):
        return []
    return [_action(kind or "tool", "other", None, at)] if kind else []


# --- Antigravity ------------------------------------------------------------------

_ANTIGRAVITY_TOOLS = {
    "run_command": ("command", "CommandLine"),
    "view_file": ("read", "AbsolutePath"),
    "list_dir": ("read", "DirectoryPath"),
    "replace_file_content": ("edit", "TargetFile"),
    "write_to_file": ("write", "TargetFile"),
    "grep_search": ("search", "Query"),
    "find_by_name": ("search", "Pattern"),
    "search_web": ("web", "query"),
    "read_url_content": ("web", "Url"),
}
_USER_REQUEST = re.compile(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", re.S)
# Images the user attached, listed in the message's metadata; and the
# output of the generate_image tool.
_UPLOADED = re.compile(r"The user has uploaded \d+ image\(s\):\n((?:- [^\n]+\n?)+)")
_LIST_ITEM = re.compile(r"^- (/[^\n]+?)\s*$", re.M)
_GENERATED = re.compile(r"Generated image is saved at (/\S+?\.(?:png|jpe?g|webp))\.?(?:\s|$)")


def _antigravity_arg(value):
    # Arguments are stored JSON-encoded: "\"git status\"".
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, str) else value
        except ValueError:
            return value
    return value


def parse_antigravity(obj: dict) -> List[dict]:
    kind, at = obj.get("type"), obj.get("created_at")
    content = obj.get("content") or ""
    if kind == "USER_INPUT":
        match = _USER_REQUEST.search(content)
        text = match.group(1) if match else content
        uploaded = _UPLOADED.search(content)
        images = [att.file(p) for p in _LIST_ITEM.findall(uploaded.group(1))] if uploaded else []
        return _with_attachments([_placet_or_user(text, at)] if text.strip() else [], images, at)
    if kind == "GENERIC":
        generated = _GENERATED.search(content)
        if generated:
            return [_entry("media", "Immagine generata", at, attachments=[att.file(generated.group(1))])]
        return []
    if kind == "PLANNER_RESPONSE":
        out = [_entry("assistant", _clip(content), at)] if content.strip() else []
        for call in obj.get("tool_calls") or []:
            name = call.get("name", "")
            args = {k: _antigravity_arg(v) for k, v in (call.get("args") or {}).items()}
            if name == "call_mcp_tool":
                out.append(_action(args.get("ToolName") or name, "mcp", args.get("ServerName"), at))
                continue
            tool_kind, key = _ANTIGRAVITY_TOOLS.get(name, ("other", None))
            detail = args.get(key) if key else None
            out.append(_action(name, tool_kind, detail or args.get("toolSummary"), at))
        return out
    if kind == "CHECKPOINT":
        return [_entry("compact", at=at)]  # the conversation was summarized here
    if kind == "ERROR_MESSAGE":
        return [_entry("notice", _clip(content, MAX_DETAIL_CHARS), at)]
    return []


# --- Locating the file --------------------------------------------------------------


def _claude_file(home: Path, session_id: str) -> List[Tuple[Path, Path]]:
    root = home / ".claude" / "projects"
    return [(root, p) for p in root.glob(f"*/{session_id}.jsonl")]


def _codex_file(home: Path, session_id: str) -> List[Tuple[Path, Path]]:
    # rollout-<date>-<id>.jsonl, or rollout-<date>-<parent>_<id>.jsonl for forks.
    name = re.compile(rf"^rollout-.*[-_]{re.escape(session_id)}(_[A-Za-z0-9-]+)?\.jsonl$")
    found = []
    for root in (home / ".codex" / "sessions", home / ".codex" / "archived_sessions"):
        found += [(root, p) for p in root.glob(f"**/rollout-*{session_id}*.jsonl") if name.match(p.name)]
    return found


def _antigravity_file(home: Path, session_id: str) -> List[Tuple[Path, Path]]:
    found = []
    for app_dir in ("antigravity", "antigravity-cli"):
        root = home / ".gemini" / app_dir / "brain"
        path = root / session_id / ".system_generated" / "logs" / "transcript.jsonl"
        if path.is_file():
            found.append((root, path))
    return found


Provider = Tuple[Callable[[Path, str], List[Tuple[Path, Path]]], Callable[[dict], List[dict]]]
PROVIDERS: Dict[str, Provider] = {
    "claude-code": (_claude_file, parse_claude),
    "codex": (_codex_file, parse_codex),
    "antigravity": (_antigravity_file, parse_antigravity),
}
# Sessions registered without a provider: guess it from the agent.
AGENT_PROVIDERS = {"claude": "claude-code", "codex": "codex", "gemini": "antigravity"}


def provider_for(provider: Optional[str], agent_id: Optional[str]) -> Optional[str]:
    if provider in PROVIDERS:
        return provider
    return AGENT_PROVIDERS.get(agent_id or "")


# (provider, session id, home) -> file already validated, to avoid globbing
# the provider's folder on every page.
_located: Dict[Tuple[str, str, Path], Path] = {}


def locate(provider: str, session_id: str, home: Optional[Path] = None) -> Path:
    if provider not in PROVIDERS:
        raise TranscriptError("unsupported", f"Provider {provider!r} is not supported.")
    if not SESSION_ID.match(session_id or ""):
        raise TranscriptError("invalid", "Invalid session id.")
    home = home or Path.home()
    key = (provider, session_id, home)
    if key in _located and _located[key].is_file():
        return _located[key]
    candidates = []
    for root, path in PROVIDERS[provider][0](home, session_id):
        real, real_root = Path(os.path.realpath(path)), Path(os.path.realpath(root))
        # A symlink must not lead outside the provider's folder.
        if real.is_file() and real.is_relative_to(real_root):
            candidates.append(real)
    if not candidates:
        raise TranscriptError("not_found", "Conversation file not found on this computer.")
    _located[key] = max(candidates, key=lambda p: p.stat().st_mtime)
    return _located[key]


# --- Paging ----------------------------------------------------------------------------


def _lines_backwards(f, end: int) -> Iterator[Tuple[int, bytes]]:
    """(offset, line) of the complete lines before `end`, newest first."""
    pos, tail = end, b""
    while pos > 0:
        size = min(BLOCK_BYTES, pos)
        pos -= size
        f.seek(pos)
        chunk = f.read(size) + tail
        lines = chunk.split(b"\n")
        tail = lines[0]  # may continue in the previous block
        offset = pos + len(chunk)
        for line in reversed(lines[1:]):
            offset -= len(line) + 1
            if line.strip():
                yield offset + 1, line
    if tail.strip():
        yield 0, tail


def _complete_end(f, size: int) -> int:
    """Offset just after the last newline: a last line without one is still being written."""
    pos = size
    while pos > 0:
        start = max(0, pos - BLOCK_BYTES)
        f.seek(start)
        cut = f.read(pos - start).rfind(b"\n")
        if cut >= 0:
            return start + cut + 1
        pos = start
    return 0


def _parse(parser: Callable[[dict], List[dict]], line: bytes) -> List[dict]:
    try:
        obj = json.loads(line)
    except ValueError:
        return []
    if not isinstance(obj, dict):
        return []
    try:
        return parser(obj)
    except (AttributeError, TypeError, KeyError):
        return []  # an unknown variant of a known record: skip it


def _with_cursor(offset: int, entries: List[dict]) -> List[dict]:
    for i, entry in enumerate(entries):
        entry["cursor"] = f"{offset}:{i}"
        if entry.get("attachments"):
            entry["attachments"] = att.public(entry["attachments"], entry["cursor"])
    return entries


def read_page(
    path: Path,
    parser: Callable[[dict], List[dict]],
    before: Optional[int] = None,
    after: Optional[int] = None,
    limit: int = DEFAULT_LIMIT,
) -> dict:
    """
    Backwards (default): up to `limit` entries ending before offset `before`
    (or the end of the file), oldest first, plus `before` for the next older
    page (None at the beginning). Forwards (`after`): every entry after that
    offset. Both return `end`, the offset to pass as `after` next time.
    A last line still being written (no newline yet) is left for later.
    """
    limit = max(1, min(int(limit), MAX_LIMIT))
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        complete = _complete_end(f, f.tell())

        if after is not None:
            start = max(0, min(int(after), complete))
            f.seek(start)
            data = f.read(min(complete - start, MAX_FORWARD_BYTES))
            stop = data.rfind(b"\n") + 1
            if stop == 0 and data:
                # One line longer than the read window (a huge tool output):
                # skip to its end. `complete` follows a newline, so one exists.
                pos = start + len(data)
                while pos < complete:
                    cut = f.read(BLOCK_BYTES).find(b"\n")
                    if cut >= 0:
                        pos += cut + 1
                        break
                    pos += BLOCK_BYTES
                return {"messages": [], "before": None, "end": min(pos, complete)}
            messages, offset = [], start
            for line in data[:stop].split(b"\n")[:-1]:
                if line.strip():
                    messages += _with_cursor(offset, _parse(parser, line))
                offset += len(line) + 1
            return {"messages": messages, "before": None, "end": start + stop}

        end = complete if before is None else max(0, min(int(before), complete))
        collected: List[List[dict]] = []
        count, oldest = 0, end
        for offset, line in _lines_backwards(f, end):
            entries = _with_cursor(offset, _parse(parser, line))
            oldest = offset
            if entries:
                collected.append(entries)
                count += len(entries)
                if count >= limit:
                    break
        messages = [e for entries in reversed(collected) for e in entries]
        return {"messages": messages, "before": oldest if oldest > 0 else None, "end": complete}


def read_transcript(
    provider: Optional[str],
    session_id: str,
    agent_id: Optional[str] = None,
    before: Optional[int] = None,
    after: Optional[int] = None,
    limit: int = DEFAULT_LIMIT,
    home: Optional[Path] = None,
) -> dict:
    """A page of a session's conversation; raises TranscriptError."""
    resolved = provider_for(provider, agent_id)
    if resolved is None:
        raise TranscriptError("unsupported", f"Provider {provider!r} is not supported.")
    path = locate(resolved, session_id, home)
    page = read_page(path, PROVIDERS[resolved][1], before=before, after=after, limit=limit)
    return {"session_id": session_id, "provider": resolved, **page}


# --- Attachments ------------------------------------------------------------------------

ATTACHMENT_ID = re.compile(r"^(\d+):(\d+):(\d+)$")


def read_attachment(
    provider: Optional[str],
    session_id: str,
    attachment_id: str,
    agent_id: Optional[str] = None,
    home: Optional[Path] = None,
) -> Tuple[str, bytes]:
    """
    (mime, bytes) of one attachment, found again by re-reading the line its
    id points to with the same parser: only what the adapter recognised there
    can be read. Raises TranscriptError or attachments.AttachmentError.
    """
    match = ATTACHMENT_ID.match(attachment_id or "")
    if not match:
        raise TranscriptError("invalid", "Invalid attachment id.")
    offset, entry_index, index = (int(g) for g in match.groups())
    resolved = provider_for(provider, agent_id)
    if resolved is None:
        raise TranscriptError("unsupported", f"Provider {provider!r} is not supported.")
    path = locate(resolved, session_id, home)
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        if offset >= f.tell():
            raise TranscriptError("not_found", "Attachment not found.")
        if offset > 0:
            f.seek(offset - 1)
            if f.read(1) != b"\n":
                raise TranscriptError("invalid", "Invalid attachment id.")
        f.seek(offset)
        line = f.readline()
    entries = _parse(PROVIDERS[resolved][1], line)
    try:
        found = entries[entry_index]["attachments"][index]
    except (IndexError, KeyError):
        raise TranscriptError("not_found", "Attachment not found.") from None
    return att.load(resolved, session_id, found, home)
