"""Phone attachments: validate bytes and store privately as the bridge user.

Names never become paths. Content-addressed files make transfer retries safe;
no message is queued until all files have been stored.
"""
import base64
import binascii
import hashlib
import os
import stat
import tempfile
from pathlib import Path

from bridge.attachments import AttachmentError, check_content

MAX_FILES = 4
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 20 * 1024 * 1024
MAX_STORED_BYTES = 256 * 1024 * 1024
SUFFIXES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "application/pdf": ".pdf"}


def validate_uploads(files):
    if not 1 <= len(files) <= MAX_FILES:
        raise AttachmentError("too_large", "Scegli da 1 a 4 allegati.")
    decoded, total = [], 0
    for item in files:
        encoded = item.get("data", "")
        if len(encoded) > ((MAX_FILE_BYTES + 2) // 3) * 4:
            raise AttachmentError("too_large", "Ogni allegato può occupare al massimo 10 MB.")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise AttachmentError("unsupported", "Allegato non leggibile.") from None
        total += len(data)
        if len(data) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            raise AttachmentError("too_large", "Limite di 10 MB per file e 20 MB per messaggio.")
        mime = check_content(data)
        # Names are display data only, never interpreted as filesystem paths.
        name = str(item.get("name") or "allegato")[:200]
        decoded.append((name, mime, data))
    return decoded


def _private_dir(path):
    path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise AttachmentError("forbidden", "La cartella degli allegati non è sicura.")
    path.chmod(0o700)


def save_uploads(session_id, files, home=None, cwd=None):
    from hooks.placet_common import active
    if not active(cwd):
        raise AttachmentError("excluded", "Project excluded (.placet-off).")
    decoded = validate_uploads(files)
    home = Path(home) if home is not None else Path.home()
    root = home
    for component in (".cache", "placet", "uploads"):
        root = root / component
        # Do not change the permissions of the user's general cache directory.
        if component == ".cache":
            root.mkdir(mode=0o700, exist_ok=True)
            if root.is_symlink() or not root.is_dir() or root.stat().st_uid != os.getuid():
                raise AttachmentError("forbidden", "Cartella cache non sicura.")
        else:
            _private_dir(root)
    folder = root / hashlib.sha256(session_id.encode()).hexdigest()
    _private_dir(folder)
    stored = sum(p.lstat().st_size for p in root.glob("*/*") if p.is_file() and not p.is_symlink())
    new_bytes = {hashlib.sha256(data).hexdigest() + SUFFIXES[mime]: len(data)
                 for _, mime, data in decoded}
    needed = sum(size for name, size in new_bytes.items() if not (folder / name).exists())
    if stored + needed > MAX_STORED_BYTES:
        raise AttachmentError("too_large", "Spazio Placet per gli allegati esaurito (256 MB).")
    result = []
    for name, mime, data in decoded:
        digest = hashlib.sha256(data).hexdigest()
        dest = folder / (digest + SUFFIXES[mime])
        if dest.exists() or dest.is_symlink():
            info = dest.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or hashlib.sha256(dest.read_bytes()).hexdigest() != digest:
                raise AttachmentError("forbidden", "Allegato salvato non valido.")
        else:
            fd, temporary = tempfile.mkstemp(prefix=".upload-", dir=folder)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, dest)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        result.append({"name": name, "mime": mime, "size": len(data), "path": str(dest)})
    return {"files": result}
