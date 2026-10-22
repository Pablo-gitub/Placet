"""Best-effort diagnosis of a Claude Desktop wake-up from the app's own log.

An undocumented adapter, isolated here on purpose: the log format is not an API and may
change with any version of the app. It is read-only, bounded, and never raises: anything it
cannot read or does not recognise is "no diagnosis". It names one cause only, as a closed
code; no line, path, argument or chat content ever leaves this module.

Observed on Claude Desktop 2.9939.4 (~/.config/Claude/logs/main.log, lines starting with
`YYYY-MM-DD HH:MM:SS [info] `): after `[CCD] Warming session local_<id>` the app's governor logs
`... at cap; yielding warm spawn` and the engine never starts, whereas a start logs
`[CCD] Session local_<id> warmed successfully in <n>ms`.
"""
import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

WARM_CAPACITY_DENIED = "warm_capacity_denied"
MAX_BYTES = 256 * 1024

_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \[info\] (.*)$")
_WARMING = re.compile(r"^\[CCD\] Warming session (local_[A-Za-z0-9-]{1,64})\s*$")
_WARMED = re.compile(r"^\[CCD\] Session (local_[A-Za-z0-9-]{1,64}) warmed successfully\b")
_YIELDED = re.compile(r"^\[CliGovernor\] at cap; yielding warm spawn\b")


@dataclass(frozen=True)
class WakeDiagnosis:
    code: str
    observed_at: datetime       # when the app logged it (UTC)


class ClaudeDesktopLog:
    def __init__(self, home=None, path=None):
        self.path = Path(path) if path is not None else Path(home or Path.home()) / ".config/Claude/logs/main.log"

    def _tail(self) -> Optional[str]:
        """The last MAX_BYTES of the log as text, or None if it is not a plain file of the user."""
        try:
            info = os.lstat(self.path)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                return None
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as handle:
                size = os.fstat(handle.fileno()).st_size
                start = max(0, size - MAX_BYTES)
                handle.seek(start)
                data = handle.read(MAX_BYTES)
        except (OSError, ValueError):
            return None
        text = data.decode("utf-8", errors="replace")
        # A read that starts mid-file begins with a cut line: never a record.
        return text.split("\n", 1)[1] if start > 0 and "\n" in text else ("" if start > 0 else text)

    def diagnose(self, local_id: str, since: float) -> Optional[WakeDiagnosis]:
        """
        `warm_capacity_denied` if, after the wake attempt (epoch seconds), the log shows a
        warm spawn of this very session followed by the governor yielding it before any
        success for it. A "would evict" hint alone is not enough.
        """
        try:
            text = self._tail()
            if not text:
                return None
            current: Optional[str] = None          # the session of the latest warm spawn
            denied: Optional[datetime] = None
            for raw in text.splitlines():
                found = _LINE.match(raw)
                if not found:
                    continue
                at = datetime.strptime(found.group(1), "%Y-%m-%d %H:%M:%S").astimezone(timezone.utc)
                if at.timestamp() < int(since):     # the log has whole seconds
                    continue
                body = found.group(2)
                warming = _WARMING.match(body)
                warmed = _WARMED.match(body)
                if warming:
                    current = warming.group(1)
                    if current == local_id:
                        denied = None               # a fresh warm spawn of ours: judge it afresh
                elif warmed and warmed.group(1) == local_id:
                    denied, current = None, None    # it started
                elif _YIELDED.match(body):
                    if current == local_id:
                        denied = at
                    current = None
            return WakeDiagnosis(WARM_CAPACITY_DENIED, denied) if denied else None
        except Exception:
            return None
