"""
Images in the conversations (RF-30): pasted by the user or produced by an
agent's tool, shown in the PWA as thumbnails.

The phone never asks for a path. A page of the conversation lists each
recognised attachment under its entry; its id is the entry's cursor plus its
position, so the bridge finds it again by re-reading that one line with the
same parser. Only attachments a provider adapter recognises are readable
(a path merely written in a message stays text), and a file must:

- live where that provider keeps this conversation's images (Antigravity:
  brain/<session>/; Codex: its clipboard files /tmp/codex-clipboard-<uuid>),
  checked on the real path, symlinks resolved;
- be a regular file owned by the user, at most MAX_BYTES;
- really be a JPEG, PNG or WebP (magic bytes, not the name), with at most
  MAX_PIXELS pixels. SVG and HTML are never served.

Files an agent shares on purpose with placet_share_file (a PDF it wrote, a
chart) follow their own rules (check_shared): anywhere in the user's home
but never in a hidden folder (.ssh, .config, ...), never in a project with
.placet-off, a regular file of the user, an image as above or a real PDF.
"""
import base64
import binascii
import os
import re
import struct
from pathlib import Path
from typing import Optional, Tuple

MAX_BYTES = 10 * 1024 * 1024
MAX_PDF_BYTES = 20 * 1024 * 1024
SHARE_TOOL = "placet_share_file"
MAX_PIXELS = 50_000_000
CODEX_CLIPBOARD = re.compile(r"^/tmp/codex-clipboard-[0-9a-f-]{36}\.(png|jpe?g|webp)$")


class AttachmentError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def inline(mime: Optional[str], data: str, name: str = "immagine") -> dict:
    """An image stored in the conversation itself (base64)."""
    return {"name": name, "_src": {"kind": "inline", "data": data or ""}}


def file(path: str, name: Optional[str] = None) -> dict:
    """An image the conversation points to, read from disk if allowed."""
    return {"name": name or os.path.basename(path), "_src": {"kind": "file", "path": path}}


def shared(path: str, title: Optional[str] = None) -> dict:
    """A file the agent shared with placet_share_file."""
    return {"name": title or os.path.basename(path or "") or "file", "_src": {"kind": "shared", "path": path or ""}}


def kind_of(attachment: dict) -> str:
    path = (attachment.get("_src") or {}).get("path") or attachment.get("name") or ""
    return "pdf" if path.lower().endswith(".pdf") else "image"


def public(attachments, cursor: str) -> list:
    """What the PWA sees: an id, a name and a kind, never where the file is."""
    return [{"id": f"{cursor}:{i}", "name": a.get("name") or "immagine", "kind": kind_of(a)}
            for i, a in enumerate(attachments)]


# --- Content checks ---------------------------------------------------------------


def sniff(data: bytes) -> Optional[str]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def dimensions(data: bytes, mime: str) -> Optional[Tuple[int, int]]:
    try:
        if mime == "image/png":
            return struct.unpack(">II", data[16:24])
        if mime == "image/webp":
            chunk = data[12:16]
            if chunk == b"VP8X":
                w = int.from_bytes(data[24:27], "little") + 1
                h = int.from_bytes(data[27:30], "little") + 1
                return w, h
            if chunk == b"VP8L":
                bits = int.from_bytes(data[21:25], "little")
                return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
            if chunk == b"VP8 ":
                w, h = struct.unpack("<HH", data[26:30])
                return w & 0x3FFF, h & 0x3FFF
            return None
        if mime == "image/jpeg":
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                length = struct.unpack(">H", data[i + 2:i + 4])[0]
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return w, h
                i += 2 + length
    except struct.error:
        return None
    return None


def check_image(data: bytes) -> str:
    """The image's real type; raises if it is not an allowed, sane image."""
    if len(data) > MAX_BYTES:
        raise AttachmentError("too_large", "Image larger than the limit.")
    mime = sniff(data)
    if mime is None:
        raise AttachmentError("unsupported", "Not a JPEG, PNG or WebP image.")
    size = dimensions(data, mime)
    if size is None or size[0] <= 0 or size[1] <= 0 or size[0] * size[1] > MAX_PIXELS:
        raise AttachmentError("unsupported", "Unreadable or oversized image.")
    return mime


def check_pdf(data: bytes) -> str:
    if len(data) > MAX_PDF_BYTES:
        raise AttachmentError("too_large", "PDF larger than the limit.")
    if not data.startswith(b"%PDF-"):
        raise AttachmentError("unsupported", "Not a PDF.")
    return "application/pdf"


def check_content(data: bytes) -> str:
    """Images as check_image; PDFs by their header."""
    return check_pdf(data) if data.startswith(b"%PDF-") else check_image(data)


# --- Where files may come from ------------------------------------------------------


def allowed_roots(provider: str, session_id: str, home: Path):
    if provider == "antigravity":
        return [home / ".gemini" / d / "brain" / session_id for d in ("antigravity", "antigravity-cli")]
    return []


def _allowed(provider: str, session_id: str, path: str, home: Path) -> Path:
    real = Path(os.path.realpath(path))
    if provider == "codex" and CODEX_CLIPBOARD.match(path) and str(real) == path:
        return real
    for root in allowed_roots(provider, session_id, home):
        if real.is_relative_to(Path(os.path.realpath(root))):
            return real
    raise AttachmentError("forbidden", "This file is not one of the conversation's images.")


def check_shared(path: str, home: Optional[Path] = None) -> Path:
    """
    Where a shared file may be: raises AttachmentError, else its real path.
    Used by the MCP tool (to tell the agent at once) and again when read.
    """
    from hooks.placet_common import active
    home = Path(os.path.realpath(home or Path.home()))
    if not path or not os.path.isabs(path):
        raise AttachmentError("forbidden", "Give the absolute path of the file.")
    real = Path(os.path.realpath(path))
    if not real.is_relative_to(home) or real == home:
        raise AttachmentError("forbidden", "Only files in your home can be shared.")
    if any(part.startswith(".") for part in real.relative_to(home).parts):
        raise AttachmentError("forbidden", "Files in hidden folders (like .ssh or .config) are never shared.")
    if not active(str(real.parent)):
        raise AttachmentError("forbidden", "This project is excluded from Placet (.placet-off).")
    try:
        st = os.stat(real)
    except OSError:
        raise AttachmentError("not_found", "The file is no longer on this computer.") from None
    if not os.path.isfile(real) or st.st_uid != os.getuid():
        raise AttachmentError("forbidden", "Not a regular file of this user.")
    if st.st_size > MAX_PDF_BYTES:
        raise AttachmentError("too_large", "File larger than the limit.")
    return real


def load(provider: str, session_id: str, attachment: dict, home: Optional[Path] = None) -> Tuple[str, bytes]:
    """(mime, bytes) of a recognised attachment; raises AttachmentError."""
    home = home or Path.home()
    src = attachment.get("_src") or {}
    if src.get("kind") == "inline":
        try:
            data = base64.b64decode(src.get("data") or "", validate=True)
        except (binascii.Error, ValueError):
            raise AttachmentError("unsupported", "Corrupted image.") from None
        return check_image(data), data
    if src.get("kind") == "shared":
        real = check_shared(src.get("path") or "", home)
        with open(real, "rb") as f:
            data = f.read(MAX_PDF_BYTES + 1)
        return check_content(data), data
    if src.get("kind") != "file":
        raise AttachmentError("not_found", "Unknown attachment.")
    real = _allowed(provider, session_id, src.get("path") or "", home)
    try:
        st = os.stat(real, follow_symlinks=False)
    except OSError:
        raise AttachmentError("not_found", "The file is no longer on this computer.") from None
    if not os.path.isfile(real) or os.path.islink(real) or st.st_uid != os.getuid():
        raise AttachmentError("forbidden", "Not a regular file of this user.")
    if st.st_size > MAX_BYTES:
        raise AttachmentError("too_large", "Image larger than the limit.")
    with open(real, "rb") as f:
        data = f.read(MAX_BYTES + 1)
    return check_image(data), data
