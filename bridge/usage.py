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
from typing import Iterable, List, Optional

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


def antigravity_usage(summary: dict, now: Optional[datetime] = None) -> Optional[dict]:
    """Buckets from Antigravity's RetrieveUserQuotaSummary (remaining fractions per model group)."""
    groups = ((summary or {}).get("response") or {}).get("groups") or []
    buckets = []
    for group in groups:
        label = group.get("displayName")
        for bucket in group.get("buckets") or []:
            window = {"5h": "5h", "weekly": "7d"}.get(bucket.get("window"))
            remaining = bucket.get("remainingFraction")
            if window is None or remaining is None:
                continue
            buckets.append({
                "id": bucket.get("bucketId") or f"{label}-{window}", "group": label, "window": window,
                "used_percent": round((1.0 - float(remaining)) * 100, 1),
                "resets_at": bucket.get("resetTime"),
            })
    if not buckets:
        return None
    return {"provider": "antigravity", "plan": None,
            "observed_at": (now or datetime.now(timezone.utc)).isoformat(), "buckets": buckets}


def read_all(antigravity=None, home: Optional[Path] = None) -> List[dict]:
    """Every provider that has something to report; a failing reader is skipped."""
    out: List[dict] = []
    readers: Iterable = (
        lambda: codex_usage(home),
        lambda: antigravity_usage(antigravity.quota_summary()) if antigravity is not None else None,
    )
    for read in readers:
        try:
            usage = read()
        except Exception:
            usage = None
        if usage:
            out.append(usage)
    return out
