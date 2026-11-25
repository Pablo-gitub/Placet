"""
Usage limits per provider (RF-60): how much of the 5-hour and weekly windows
is used, and when each resets. Read by the bridge, which runs as the user.

Each reader returns {"provider", "plan", "observed_at", "buckets": [...]} with
buckets {"id", "group", "window" ("5h" | "7d"), "used_percent", "resets_at"}.
Times are ISO 8601 UTC. A reader returns None when it has nothing to say.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

# Rollouts are long: the latest limits are near the end.
TAIL_BYTES = 256 * 1024
# Only the most recently written rollouts are looked at.
MAX_ROLLOUTS = 5
WINDOWS = {300: "5h", 10080: "7d"}


def _iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()


def _tail_lines(path: Path) -> List[str]:
    with path.open("rb") as f:
        size = f.seek(0, os.SEEK_END)
        f.seek(max(0, size - TAIL_BYTES))
        data = f.read().decode("utf-8", errors="replace")
    lines = data.splitlines()
    return lines[1:] if size > TAIL_BYTES else lines


def _recent_rollouts(home: Path) -> List[Path]:
    root = home / ".codex" / "sessions"
    if not root.is_dir():
        return []
    files = [p for p in root.glob("*/*/*/rollout-*.jsonl") if not p.is_symlink()]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:MAX_ROLLOUTS]


def codex_usage(home: Optional[Path] = None) -> Optional[dict]:
    """The latest `rate_limits` Codex wrote into any rollout (they belong to the account)."""
    best = None
    for path in _recent_rollouts(Path(home or Path.home())):
        try:
            lines = _tail_lines(path)
        except OSError:
            continue
        for line in reversed(lines):
            if '"rate_limits"' not in line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            limits = (entry.get("payload") or {}).get("rate_limits")
            # Several limits are reported (e.g. "codex", "premium"); only those
            # with time windows say how much of the 5h / 7d windows is used.
            if not isinstance(limits, dict) or not (limits.get("primary") or limits.get("secondary")):
                continue
            stamp = entry.get("timestamp") or ""
            if best is None or stamp > best[0]:
                best = (stamp, limits)
            break
    if best is None:
        return None
    stamp, limits = best
    buckets = []
    for key in ("primary", "secondary"):
        window = limits.get(key)
        if not isinstance(window, dict) or window.get("used_percent") is None:
            continue
        name = WINDOWS.get(window.get("window_minutes"))
        if name is None:
            continue
        buckets.append({
            "id": name, "group": None, "window": name,
            "used_percent": float(window["used_percent"]),
            "resets_at": _iso(window.get("resets_at")),
        })
    if not buckets:
        return None
    return {"provider": "codex", "plan": limits.get("plan_type"), "observed_at": stamp or None,
            "buckets": buckets}


def read_all(home: Optional[Path] = None) -> List[dict]:
    """Report the available usage windows from Codex."""
    try:
        usage = codex_usage(home)
    except (OSError, TypeError, ValueError, OverflowError):
        return []
    return [usage] if usage else []
