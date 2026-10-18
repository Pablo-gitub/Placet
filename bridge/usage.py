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


CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
# Claude's usage is asked to Anthropic: less often than the local readers.
CLAUDE_EVERY_SECONDS = 300.0
CLAUDE_WINDOWS = (("five_hour", "5h", None), ("seven_day", "7d", None),
                  ("seven_day_opus", "7d", "Opus"), ("seven_day_sonnet", "7d", "Sonnet"))


def claude_usage(home: Optional[Path] = None, get=None) -> Optional[dict]:
    """
    Claude's usage windows, asked to Anthropic as Claude Code's `/usage` does,
    with the OAuth token Claude Code keeps in ~/.claude/.credentials.json
    (undocumented endpoint). The token goes only to Anthropic, and is never
    refreshed here: if Claude Code has not refreshed it, nothing is read.
    """
    path = Path(home or Path.home()) / ".claude" / ".credentials.json"
    try:
        oauth = json.loads(path.read_text()).get("claudeAiOauth") or {}
    except (OSError, ValueError):
        return None
    token, expires = oauth.get("accessToken"), oauth.get("expiresAt")
    if not token or (expires and expires / 1000 <= datetime.now(timezone.utc).timestamp()):
        return None
    if get is None:
        import httpx
        get = lambda: httpx.get(CLAUDE_USAGE_URL, timeout=15.0, headers={  # noqa: E731
            "Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20"})
    resp = get()
    if resp.status_code != 200:
        return None
    data = resp.json()
    buckets = []
    for key, window, group in CLAUDE_WINDOWS:
        item = data.get(key)
        if not isinstance(item, dict) or item.get("utilization") is None:
            continue
        buckets.append({"id": key, "group": group, "window": window,
                        "used_percent": min(100.0, max(0.0, float(item["utilization"]))),
                        "resets_at": item.get("resets_at")})
    if not buckets:
        return None
    return {"provider": "claude-code", "plan": oauth.get("subscriptionType"),
            "observed_at": datetime.now(timezone.utc).isoformat(), "buckets": buckets}


def read_all(antigravity=None, home: Optional[Path] = None, claude=None) -> List[dict]:
    """Every provider that has something to report; a failing reader is skipped."""
    out: List[dict] = []
    readers: Iterable = (
        lambda: claude() if claude is not None else None,
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
