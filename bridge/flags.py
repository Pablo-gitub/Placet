"""
Settings a hook may not ask the backend about, mirrored for it as files (RS-09: the session credential).

Hooks spool and exit, the backend runs as another user, and the bridge runs as the user: so the bridge writes a
flag file and the hook stats it. Nothing but these flags is ever written here, and only to a private directory.
"""
import logging
import os
import stat
from pathlib import Path
from typing import Optional

from hooks import placet_common

logger = logging.getLogger(__name__)
FLAG_NAME = "session-credentials"


def _private_dir(directory: Path, create: bool) -> bool:
    """The directory exists as ours alone (0700, not a link); with `create`, make it."""
    try:
        if create:
            directory.parent.mkdir(parents=True, exist_ok=True)
            try:
                directory.mkdir(mode=0o700)
            except FileExistsError:
                pass
        info = os.lstat(directory)
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077


def mirror_flag(on: bool, flags_dir: Optional[Path] = None, name: str = FLAG_NAME) -> None:
    """Make the flag file exist exactly when `on`. Atomic, private, never follows a link, never raises."""
    directory = Path(flags_dir) if flags_dir is not None else placet_common.FLAGS_DIR
    path = directory / name
    try:
        if not on:
            if os.path.lexists(path) and _private_dir(directory, create=False):
                os.unlink(path)                      # a link is removed itself, never followed
            return
        if not _private_dir(directory, create=True):
            logger.warning("The flags directory is not private: no flag written.")
            return
        try:
            info = os.lstat(path)
            if stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o600:
                return                               # already there
        except FileNotFoundError:
            pass
        temporary = directory / f".{name}.{os.getpid()}.tmp"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write("1\n")
        os.replace(temporary, path)                  # replaces a link or a stale file by the new regular one
    except OSError as exc:
        logger.warning("Could not mirror the %s flag (%s).", name, type(exc).__name__)
