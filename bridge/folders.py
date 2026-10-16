"""
Which project folders go through Placet (opt-in per project).

The hooks are installed for every session but stay silent unless the
project has a `.placet-on` file (in the folder or one containing it; the
closest marker wins, `.placet-off` keeps a subfolder out). The bridge, which
runs as the user, lists the folders the providers know and switches them on
or off for the PWA; it only ever creates or removes those two marker files,
inside the user's home.
"""
import re
import tomllib
from pathlib import Path
from typing import Dict, Iterable, List, Optional

ON_MARKER = ".placet-on"
OFF_MARKER = ".placet-off"
MAX_CODEX_ROLLOUTS = 300


class FolderError(Exception):
    """`code`: invalid or not_found."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def state_of(folder: Path) -> str:
    """on / inherited (an enclosing folder is on) / excluded (`.placet-off`) / off."""
    for current in (folder, *folder.parents):
        if (current / OFF_MARKER).exists():
            return "excluded" if current == folder else "off"
        if (current / ON_MARKER).exists():
            return "on" if current == folder else "inherited"
    return "off"


def _first_cwd(path: Path, lines: int = 40) -> Optional[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for _, line in zip(range(lines), f):
                match = re.search(r'"cwd"\s*:\s*"([^"]+)"', line)
                if match:
                    return match.group(1)
    except OSError:
        pass
    return None


def _claude_folders(home: Path) -> Iterable[tuple]:
    for project in (home / ".claude" / "projects").glob("*"):
        files = sorted(project.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        if files:
            cwd = _first_cwd(files[0])
            if cwd:
                yield cwd, "claude-code", files[0].stat().st_mtime


def _codex_folders(home: Path) -> Iterable[tuple]:
    try:
        config = tomllib.loads((home / ".codex" / "config.toml").read_text())
        for path in (config.get("projects") or {}):
            yield path, "codex", 0.0
    except (OSError, tomllib.TOMLDecodeError):
        pass
    rollouts = sorted((home / ".codex" / "sessions").glob("*/*/*/rollout-*.jsonl"),
                      key=lambda p: p.name, reverse=True)[:MAX_CODEX_ROLLOUTS]
    for path in rollouts:
        cwd = _first_cwd(path, lines=1)
        if cwd:
            yield cwd, "codex", path.stat().st_mtime


def _antigravity_folders(home: Path) -> Iterable[tuple]:
    summaries = home / ".gemini" / "antigravity" / "agyhub_summaries_proto.pb"
    try:
        data = summaries.read_bytes()
    except OSError:
        return
    # Paths inside the conversation summaries (protobuf): no per-folder date.
    prefix = re.escape(str(home).encode())
    for match in set(re.findall(rb"(" + prefix + rb"/[A-Za-z0-9_.@+/-]+)", data)):
        yield match.decode(), "antigravity", 0.0


def known_folders(home: Optional[Path] = None, extra: Iterable[str] = ()) -> List[dict]:
    """
    The project folders the providers have used (and `extra`, e.g. those of
    registered sessions), with their Placet state, most recently used first.
    """
    home = (home or Path.home()).resolve()
    found: Dict[Path, dict] = {}
    sources = [*_claude_folders(home), *_codex_folders(home), *_antigravity_folders(home),
               *((path, None, 0.0) for path in extra if path)]
    for raw, provider, when in sources:
        try:
            folder = Path(raw).resolve()
        except (OSError, RuntimeError):
            continue
        if folder == home or not folder.is_relative_to(home) or not folder.is_dir():
            continue
        entry = found.setdefault(folder, {"path": str(folder), "providers": set(), "last_used": 0.0})
        if provider:
            entry["providers"].add(provider)
        entry["last_used"] = max(entry["last_used"], when)
    folders = []
    for folder, entry in found.items():
        folders.append({**entry, "providers": sorted(entry["providers"]), "state": state_of(folder),
                        "name": folder.name})
    return sorted(folders, key=lambda f: (-f["last_used"], f["path"]))


def set_folder(path: str, active: bool, home: Optional[Path] = None) -> dict:
    """Switches a project folder on or off by its marker files only."""
    home = (home or Path.home()).resolve()
    try:
        folder = Path(path).expanduser().resolve()
    except (OSError, RuntimeError):
        raise FolderError("invalid", "Percorso non valido.") from None
    if folder == home or not folder.is_relative_to(home):
        raise FolderError("invalid", "Solo cartelle dentro la tua home, e non la home stessa.")
    if not folder.is_dir():
        raise FolderError("not_found", "La cartella non esiste su questo PC.")
    on, off = folder / ON_MARKER, folder / OFF_MARKER
    if active:
        off.unlink(missing_ok=True)
        if state_of(folder) not in ("on", "inherited"):
            on.touch()
    else:
        on.unlink(missing_ok=True)
        if state_of(folder) == "inherited":
            off.touch()  # an enclosing project is on: keep this one out
    return {"path": str(folder), "name": folder.name, "state": state_of(folder)}

