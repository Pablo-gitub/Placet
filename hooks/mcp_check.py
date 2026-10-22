"""
RF-27: is the Placet MCP server configured for this provider? (stdlib only)

Read-only look at the provider's own configuration. Only whether an entry
named like `placet` exists is kept: no value of the files is read further,
stored or sent, and nothing is written.

  configured  an entry for Placet was found
  missing     at least one configuration was read and none lists Placet
  unknown     nothing could be read (absent or broken files, other provider)

"missing" can be wrong when the server comes from somewhere not looked at
here (a plugin, managed settings): the caller words the warning accordingly.
"""
import json
import os
from pathlib import Path
from typing import Iterable, Optional

try:
    import tomllib
except ImportError:  # Python < 3.11
    tomllib = None


def _lists_placet(servers) -> bool:
    return isinstance(servers, dict) and any("placet" in str(name).lower() for name in servers)


def _json(path: Path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _folders(cwd: Optional[str]) -> Iterable[Path]:
    if not cwd:
        return []
    try:
        folder = Path(cwd).resolve()
    except (OSError, RuntimeError):
        return []
    return [folder, *folder.parents]


def _verdict(read: int, found: bool) -> str:
    if found:
        return "configured"
    return "missing" if read else "unknown"


def _claude(cwd, home: Path) -> str:
    read, found = 0, False
    data = _json(home / ".claude.json")
    if data is not None:
        read += 1
        found = _lists_placet(data.get("mcpServers"))
        projects = data.get("projects")
        for folder in _folders(cwd):
            entry = projects.get(str(folder)) if isinstance(projects, dict) else None
            if isinstance(entry, dict) and _lists_placet(entry.get("mcpServers")):
                found = True
    for folder in _folders(cwd):
        project = _json(folder / ".mcp.json")
        if project is not None:
            read += 1
            found = found or _lists_placet(project.get("mcpServers"))
    return _verdict(read, found)


def _codex(cwd, home: Path, env) -> str:
    if tomllib is None:
        return "unknown"
    codex_home = Path(env.get("CODEX_HOME") or home / ".codex")
    read, found = 0, False
    paths = [codex_home / "config.toml"] + [folder / ".codex" / "config.toml" for folder in _folders(cwd)]
    for path in paths:
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        read += 1
        found = found or _lists_placet(data.get("mcp_servers"))
    return _verdict(read, found)


def _antigravity(home: Path) -> str:
    # Antigravity 2.0 reads ~/.gemini/config/mcp_config.json, not ~/.gemini/antigravity/.
    data = _json(home / ".gemini" / "config" / "mcp_config.json")
    if data is None:
        return "unknown"
    return _verdict(1, _lists_placet(data.get("mcpServers")))


def mcp_state(provider: Optional[str], cwd: Optional[str], home: Optional[Path] = None, env=None) -> str:
    home = Path(home) if home is not None else Path.home()
    env = os.environ if env is None else env
    try:
        if provider == "claude-code":
            return _claude(cwd, home)
        if provider == "codex":
            return _codex(cwd, home, env)
        if provider == "antigravity":
            return _antigravity(home)
    except Exception:
        pass  # a hook must never fail on this
    return "unknown"
