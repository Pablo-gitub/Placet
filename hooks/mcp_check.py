"""
RF-27: does the provider's configuration list the Placet MCP server? (stdlib only)

Read-only look at the provider's own configuration files. Only whether an
entry named like `placet` exists, and whether it is switched off, is kept: no
other value is read, stored or sent, and nothing is written.

  configured  an enabled entry for Placet was found in the files checked
  missing     at least one file was read and none lists Placet
  unknown     nothing could be read (absent or broken files, other provider),
              or the entry is switched off, refused or still waiting for approval
              (a project's `.mcp.json` server needs the user's approval, recorded in
              ~/.claude.json); an "off" anywhere wins over an entry found elsewhere

These describe the CONFIGURATION that was detected, never the connection: a
configured server can still fail to start, and "missing" only means "not found
in the files looked at" (a plugin or managed settings are not looked at). The
connection itself is only known when a Placet MCP call is observed (backend,
`mcp_seen_at`). The wording shown to people and agents must stay that careful.
"""
import json
import os
from pathlib import Path
from typing import Iterable, Optional

try:
    import tomllib
except ImportError:  # Python < 3.11
    tomllib = None


def _placet_entries(servers):
    """The Placet entries of an mcpServers-like table, as {name: entry}."""
    if not isinstance(servers, dict):
        return {}
    return {name: entry for name, entry in servers.items() if "placet" in str(name).lower()}


def _switched_off(entry) -> bool:
    return isinstance(entry, dict) and (entry.get("enabled") is False or entry.get("disabled") is True)


def _lists_placet(servers) -> bool:
    """Whether an ENABLED Placet entry is there."""
    return any(not _switched_off(entry) for entry in _placet_entries(servers).values())


def _lists_disabled_placet(servers) -> bool:
    entries = _placet_entries(servers)
    return bool(entries) and all(_switched_off(entry) for entry in entries.values())


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


def _verdict(read: int, found: bool, off: bool = False) -> str:
    if off:
        return "unknown"  # an entry exists but is switched off: what applies is not clear
    if found:
        return "configured"
    return "missing" if read else "unknown"


def _names(value) -> set:
    return {str(n) for n in value} if isinstance(value, list) else set()


def _claude(cwd, home: Path) -> str:
    """
    Servers in ~/.claude.json (user scope, and per project) run as configured; servers
    from a project's `.mcp.json` run only after the user approved them, which Claude Code
    records per project in ~/.claude.json: `enabledMcpjsonServers` (approved),
    `disabledMcpjsonServers` (refused), `enableAllProjectMcpServers`. A `.mcp.json`
    entry refused, not approved yet, or whose approvals cannot be read, makes the
    answer "unknown", and an `off` anywhere wins over an entry found elsewhere.
    """
    read, found, off = 0, False, False
    approved, refused, approve_all = set(), set(), False
    data = _json(home / ".claude.json")
    if data is not None:
        read += 1
        found = _lists_placet(data.get("mcpServers"))
        off = _lists_disabled_placet(data.get("mcpServers"))
        projects = data.get("projects")
        for folder in _folders(cwd):
            entry = projects.get(str(folder)) if isinstance(projects, dict) else None
            if isinstance(entry, dict):
                found = found or _lists_placet(entry.get("mcpServers"))
                off = off or _lists_disabled_placet(entry.get("mcpServers"))
                approved |= _names(entry.get("enabledMcpjsonServers"))
                refused |= _names(entry.get("disabledMcpjsonServers"))
                approve_all = approve_all or entry.get("enableAllProjectMcpServers") is True
    for folder in _folders(cwd):
        project = _json(folder / ".mcp.json")
        if project is None:
            continue
        read += 1
        for name, entry in _placet_entries(project.get("mcpServers")).items():
            if _switched_off(entry) or name in refused:
                off = True
            elif approve_all or name in approved:
                found = True
            else:
                off = True  # not approved yet (or the approvals are unreadable)
    return _verdict(read, found, off)


def _codex(cwd, home: Path, env) -> str:
    if tomllib is None:
        return "unknown"
    codex_home = Path(env.get("CODEX_HOME") or home / ".codex")
    read, found, off = 0, False, False
    paths = [codex_home / "config.toml"] + [folder / ".codex" / "config.toml" for folder in _folders(cwd)]
    for path in paths:
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        read += 1
        found = found or _lists_placet(data.get("mcp_servers"))
        off = off or _lists_disabled_placet(data.get("mcp_servers"))
    return _verdict(read, found, off)


def _antigravity(home: Path) -> str:
    # Antigravity 2.0 reads ~/.gemini/config/mcp_config.json, not ~/.gemini/antigravity/.
    data = _json(home / ".gemini" / "config" / "mcp_config.json")
    if data is None:
        return "unknown"
    servers = data.get("mcpServers")
    return _verdict(1, _lists_placet(servers), _lists_disabled_placet(servers))


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
