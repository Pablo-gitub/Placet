#!/usr/bin/env python3
"""
Installs the Placet hooks for every session of the user's providers (RF-42),
so that each new session registers by itself:

- Claude Code (app and CLI): ~/.claude/settings.json
- Codex (app and CLI):       ~/.codex/hooks.json   (approve once with /hooks in Codex)
- Antigravity (app and agy): ~/.gemini/config/hooks.json

Only Placet's own entries are touched: they are recognised by the hook
scripts they run, replaced on every run (so running it twice changes
nothing) and removed with --uninstall. Each file is backed up before it
changes.

Usage: python3 install.py [--providers claude,codex,antigravity]
                          [--hooks-dir /opt/placet/hooks] [--uninstall] [--dry-run]
"""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path

SCRIPTS = ("claude_code.py", "placet_waiter.py", "antigravity.py", "steer.py")
HOME = Path.home()


def _is_placet(command: str, hooks_dir: str) -> bool:
    return any(f"{hooks_dir}/{script}" in command or f"/hooks/{script}" in command for script in SCRIPTS)


def _command(hooks_dir: str, script: str, *args: str) -> str:
    return " ".join(["python3", f"{hooks_dir}/{script}", *args])


# --- Claude Code and Codex: {"hooks": {Event: [{"matcher"?, "hooks": [...]}]}} -----


def _grouped(hooks: dict, hooks_dir: str) -> dict:
    """Removes Placet handlers from a Claude-style hooks object."""
    cleaned = {}
    for event, groups in hooks.items():
        kept_groups = []
        for group in groups if isinstance(groups, list) else []:
            handlers = [h for h in group.get("hooks", []) if not _is_placet(h.get("command", ""), hooks_dir)]
            if handlers:
                kept_groups.append({**group, "hooks": handlers})
        if kept_groups:
            cleaned[event] = kept_groups
    return cleaned


def claude_hooks(hooks_dir: str) -> dict:
    hook = {"type": "command", "command": _command(hooks_dir, "claude_code.py", "--agent", "claude"), "timeout": 5}
    waiter = {"type": "command", "command": _command(hooks_dir, "placet_waiter.py", "--agent", "claude"),
              "async": True, "asyncRewake": True, "timeout": 7 * 24 * 3600}
    # "Subito" (RF-35): after every action, but Python starts only when a
    # message is waiting for some session.
    steer = {"type": "command", "timeout": 5, "command": (
        f"sh -c '[ -n \"$(ls -A \"$HOME/.cache/placet/steer\" 2>/dev/null)\" ] "
        f"&& exec {_command(hooks_dir, 'steer.py', '--agent', 'claude')} || cat >/dev/null'"
    )}
    return {
        "SessionStart": [{"hooks": [hook, waiter]}],
        "UserPromptSubmit": [{"hooks": [hook]}],
        "PostToolUse": [{"matcher": "mcp__placet__.*", "hooks": [hook]}, {"hooks": [steer]}],
        "Stop": [{"hooks": [hook, waiter]}],
        "SessionEnd": [{"hooks": [hook]}],
    }


def codex_hooks(hooks_dir: str) -> dict:
    # Codex runs no async hooks: the bridge wakes its sessions (codex queue).
    hook = {"type": "command", "timeout": 5,
            "command": _command(hooks_dir, "claude_code.py", "--agent", "codex", "--provider", "codex")}
    return {event: [{"hooks": [hook]}] for event in
            ("SessionStart", "UserPromptSubmit", "PostToolUse", "Stop", "SessionEnd")}


def merge_grouped(current: dict, ours: dict, hooks_dir: str, uninstall: bool) -> dict:
    hooks = _grouped(current.get("hooks") or {}, hooks_dir)
    if not uninstall:
        for event, groups in ours.items():
            hooks[event] = hooks.get(event, []) + groups
    result = {**current, "hooks": hooks}
    if not hooks:
        result.pop("hooks")
    return result


# --- Antigravity: {name: {"enabled"?, Event: [handler | {"matcher", "hooks"}]}} ---------

ANTIGRAVITY_NAME = "placet"


def antigravity_hooks(hooks_dir: str) -> dict:
    def handler(event):
        return {"type": "command", "timeout": 10,
                "command": _command(hooks_dir, "antigravity.py", "--agent", "gemini", "--event", event)}
    return {ANTIGRAVITY_NAME: {event: [handler(event)] for event in ("SessionStart", "PreInvocation", "Stop")}}


def merge_antigravity(current: dict, ours: dict, hooks_dir: str, uninstall: bool) -> dict:
    result = {}
    for name, spec in current.items():
        if name == ANTIGRAVITY_NAME:
            continue
        if isinstance(spec, dict):
            events = {}
            for event, handlers in spec.items():
                if event == "enabled" or not isinstance(handlers, list):
                    events[event] = handlers
                    continue
                kept = [h for h in handlers
                        if not _is_placet(h.get("command", "") if isinstance(h, dict) else "", hooks_dir)]
                if kept:
                    events[event] = kept
            spec = events
        result[name] = spec
    if not uninstall:
        result.update(ours)
    return result


# --- Files -------------------------------------------------------------------------------

PROVIDERS = {
    "claude": (HOME / ".claude" / "settings.json", claude_hooks, merge_grouped),
    "codex": (HOME / ".codex" / "hooks.json", codex_hooks, merge_grouped),
    "antigravity": (HOME / ".gemini" / "config" / "hooks.json", antigravity_hooks, merge_antigravity),
}


def load(path: Path) -> dict:
    try:
        text = path.read_text()
    except FileNotFoundError:
        return {}
    if not text.strip():
        return {}
    data = json.loads(text)  # a broken file is an error: never overwrite it blindly
    if not isinstance(data, dict):
        raise ValueError(f"{path} does not contain a JSON object")
    return data


def apply(provider: str, hooks_dir: str, uninstall: bool, dry_run: bool, home: Path = HOME) -> str:
    path, ours, merge = PROVIDERS[provider]
    path = home / path.relative_to(HOME)
    current = load(path)
    updated = merge(current, ours(hooks_dir), hooks_dir, uninstall)
    if updated == current:
        return f"{provider}: {path} già a posto"
    if dry_run:
        return f"{provider}: {path} verrebbe aggiornato\n{json.dumps(updated, indent=2)}"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_name(f"{path.name}.bak-placet-{time.strftime('%Y%m%d%H%M%S')}")
        shutil.copy2(path, backup)
    path.write_text(json.dumps(updated, indent=2) + "\n")
    return f"{provider}: {path} {'ripulito' if uninstall else 'aggiornato'}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--providers", default="claude,codex,antigravity")
    parser.add_argument("--hooks-dir", default="/opt/placet/hooks")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    hooks_dir = args.hooks_dir.rstrip("/")
    status = 0
    for provider in [p.strip() for p in args.providers.split(",") if p.strip()]:
        if provider not in PROVIDERS:
            print(f"{provider}: provider sconosciuto", file=sys.stderr)
            status = 2
            continue
        try:
            print(apply(provider, hooks_dir, args.uninstall, args.dry_run))
        except (OSError, ValueError) as e:
            print(f"{provider}: {e}", file=sys.stderr)
            status = 1
    if not args.uninstall and not args.dry_run and "codex" in args.providers:
        print("Codex: apri una sessione CLI (`codex`) e approva gli hook di Placet con /hooks.")
    return status


if __name__ == "__main__":
    sys.exit(main())
