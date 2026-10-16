"""
Titles of Codex and Antigravity conversations, whose hooks carry none: the
PWA shows them like Claude's, instead of "project · opened at".

- Codex appends {"id", "thread_name", "updated_at"} to
  ~/.codex/session_index.jsonl at each rename: the last line of an id wins.
- Antigravity gives them through its language server (channels.py, where
  its undocumented API stays isolated).
"""
import json
import os
from pathlib import Path
from typing import Dict, Iterable, Optional

INDEX_TAIL_BYTES = 4 * 1024 * 1024


def codex_titles(ids: Iterable[str], home: Optional[Path] = None) -> Dict[str, str]:
    """The current title of each of these Codex threads, when it has one."""
    wanted = set(ids)
    if not wanted:
        return {}
    path = (home or Path.home()) / ".codex" / "session_index.jsonl"
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            start = max(0, f.tell() - INDEX_TAIL_BYTES)
            f.seek(start)
            lines = f.read().split(b"\n")
    except OSError:
        return {}
    if start:
        lines = lines[1:]  # the first line is cut
    titles: Dict[str, str] = {}
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("id") in wanted:
            name = str(entry.get("thread_name") or "").strip()
            if name:
                titles[entry["id"]] = name
    return titles
