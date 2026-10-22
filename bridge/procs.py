"""RF-62: the /proc questions the bridge asks to prove who runs in a pane (Linux)."""
from pathlib import Path
from typing import Optional


class Procs:
    def __init__(self, root: Path = Path("/proc")):
        self.root = Path(root)

    def _stat(self, pid: int) -> Optional[list]:
        try:
            return (self.root / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()
        except (OSError, IndexError):
            return None

    def alive(self, pid: int, started: str) -> bool:
        """The process exists, is not a zombie, and started when the hook saw it start."""
        fields = self._stat(pid)
        return bool(fields) and len(fields) > 19 and fields[0] != "Z" and fields[19] == str(started)

    def descends(self, pid: int, ancestor: int) -> bool:
        """Whether `pid` is `ancestor` or one of its descendants."""
        for _ in range(64):
            if pid == ancestor:
                return True
            fields = self._stat(pid)
            if not fields or len(fields) < 2:
                return False
            try:
                pid = int(fields[1])
            except ValueError:
                return False
            if pid <= 1:
                return False
        return False
