"""How many Claude Desktop engines are loaded, against the app's cap (Linux, /proc, numbers only).

Undocumented and isolated, like the log adapter. The app keeps one engine process per loaded
conversation and, above its cap, does not warm another one for a link wake-up. It also spawns
short-lived "temporary query" engines several times a minute, which do not count: only
processes that have lived a while do. Nothing but two numbers and the host ids of loaded conversations leave this module; it reads
only the user's own processes, signals none, and never raises.
"""
import os
import re
from pathlib import Path
from typing import Optional

# The app's cap as observed in Claude Desktop 2.9939.4: max(6, floor(total RAM / 3 GiB)).
CAP_MIN = 6
CAP_GIB_PER_SESSION = 3
MIN_AGE_SECONDS = 10.0
MAX_PROCESSES = 5000
MAX_READ = 256 * 1024
ENTRYPOINT = b"CLAUDE_CODE_ENTRYPOINT=claude-desktop"
HOST_ID_PREFIX = b"CLAUDE_CODE_HOST_SESSION_ID="
HOST_ID = re.compile(r"local_[0-9a-f-]{36}\Z")


class EngineCounter:
    def __init__(self, proc_root=Path("/proc"), clk_tck: Optional[int] = None):
        self.root = Path(proc_root)
        self.clk_tck = clk_tck or (os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100)

    def _read(self, path: Path) -> Optional[bytes]:
        """At most MAX_READ bytes; None if it is bigger (not claimed) or unreadable."""
        try:
            with open(path, "rb") as handle:
                data = handle.read(MAX_READ + 1)
        except OSError:
            return None
        return None if len(data) > MAX_READ else data

    def _age(self, folder: Path, uptime: float) -> Optional[float]:
        try:
            fields = (folder / "stat").read_text().rsplit(")", 1)[1].split()
            return uptime - int(fields[19]) / self.clk_tck
        except (OSError, IndexError, ValueError):
            return None

    def _is_engine(self, argv: list) -> bool:
        if not argv or b"claude" not in os.path.basename(argv[0]).lower():
            return False
        return (b"--output-format=stream-json" in argv
                or any(a == b"--output-format" and i + 1 < len(argv) and argv[i + 1] == b"stream-json"
                       for i, a in enumerate(argv)))

    def _engines(self, min_age: float):
        """
        Yields the environ of each engine of the app alive for at least `min_age` seconds; None if /proc
        cannot tell. The environ never leaves this class: callers take one value out of it, or none.
        """
        try:
            uptime = float((self.root / "uptime").read_text().split()[0])
            names = [n for n in os.listdir(self.root) if n.isdigit()][:MAX_PROCESSES]
        except (OSError, IndexError, ValueError):
            return None
        found = []
        for name in names:
            folder = self.root / name
            try:
                if os.stat(folder).st_uid != os.getuid():
                    continue                                     # only our own processes
                cmdline = self._read(folder / "cmdline")
                if cmdline is None or not self._is_engine(cmdline.rstrip(b"\0").split(b"\0")):
                    continue
                environ = self._read(folder / "environ")
                if environ is None:
                    continue
                entries = environ.split(b"\0")
                if ENTRYPOINT not in entries:
                    continue
                age = self._age(folder, uptime)
                if age is None or age < min_age:
                    continue                                     # a temporary query, or unknown
                found.append(entries)
            except Exception:
                continue
        return found

    def count(self, min_age: float = MIN_AGE_SECONDS) -> Optional[int]:
        """Engines of the app, alive for at least `min_age` seconds; None if /proc cannot tell."""
        engines = self._engines(min_age)
        return None if engines is None else len(engines)

    def loaded(self, min_age: float = MIN_AGE_SECONDS) -> Optional[dict]:
        """
        {"ids": host ids of the loaded conversations, "unknown": engines without a valid one}. Of each
        environment only the value of CLAUDE_CODE_HOST_SESSION_ID is kept, and only if it is
        `local_<uuid>`: the rest (an OAuth token lives there) is never returned, logged or stored.
        """
        engines = self._engines(min_age)
        if engines is None:
            return None
        ids, unknown = set(), 0
        for entries in engines:
            host = next((e[len(HOST_ID_PREFIX):] for e in entries if e.startswith(HOST_ID_PREFIX)), None)
            try:
                text = host.decode("ascii") if host is not None else ""
            except UnicodeDecodeError:
                text = ""
            if HOST_ID.fullmatch(text):
                ids.add(text)
            else:
                unknown += 1
        return {"ids": ids, "unknown": unknown}

    def cap(self) -> Optional[int]:
        try:
            for line in (self.root / "meminfo").read_text().splitlines():
                if line.startswith("MemTotal:"):
                    gib = int(line.split()[1]) * 1024 / (1024 ** 3)
                    return max(CAP_MIN, int(gib // CAP_GIB_PER_SESSION))
        except (OSError, ValueError, IndexError):
            pass
        return None

    def snapshot(self) -> Optional[dict]:
        """{"engines": n, "cap": c}, or None when this platform or /proc cannot tell."""
        try:
            engines, cap = self.count(), self.cap()
        except Exception:
            return None
        return {"engines": engines, "cap": cap} if engines is not None and cap is not None else None
