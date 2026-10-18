"""
Local event queue of the Placet hooks (docs/architecture.md, section 3.6; stdlib only).

Hooks never call the backend: they write the event here and exit, so a
backend that is down, slow or restarting never delays a turn nor loses its
facts. The bridge is the only consumer: it forwards the files in order and
deletes each one once the backend has stored it.

- Folder ~/.cache/placet/spool (PLACET_SPOOL_DIR), 0700; files 0600.
- Atomic write: a hidden temporary file in the same folder, then rename.
  No fsync: an event may be lost on a power cut, nothing else.
- File name <boot_id>-<monotonic ns>-<event_id>.json; the event carries
  boot_id and seq too, so the order within a boot is the observed one.
- Past MAX_SPOOL_BYTES new events are dropped (the bridge is not draining).
"""
import json
import os
import time
import uuid
from pathlib import Path
from typing import Optional

MAX_SPOOL_BYTES = 50 * 1024 * 1024
FAILED = "failed"


def spool_dir() -> Path:
    override = os.environ.get("PLACET_SPOOL_DIR")
    return Path(override) if override else Path.home() / ".cache" / "placet" / "spool"


def boot_id() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip().replace("-", "")[:12]
    except OSError:
        return "noboot"


def _size(folder: Path) -> int:
    total = 0
    for entry in os.scandir(folder):
        if entry.is_file(follow_symlinks=False):
            total += entry.stat(follow_symlinks=False).st_size
    return total


def write_event(agent: str, session_id: str, body: dict, folder: Optional[Path] = None) -> Optional[Path]:
    """Queue one hook event; returns its file, or None if it was dropped."""
    folder = folder or spool_dir()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    if _size(folder) > MAX_SPOOL_BYTES:
        return None
    boot, seq, event_id = boot_id(), time.monotonic_ns(), uuid.uuid4().hex
    event = {
        "event_id": event_id, "boot_id": boot, "seq": seq, "at": time.time(),
        "agent": agent, "session_id": session_id, "body": body,
    }
    name = f"{boot}-{seq:020d}-{event_id}.json"
    tmp = folder / f".{name}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(event, f, ensure_ascii=False)
    final = folder / name
    os.replace(tmp, final)
    return final
