"""
RF-62: the only text Placet ever types into a CLI session for /compact and
/effort. The phone chooses a command and a level; the string is built here,
from fixed pieces, on both sides (backend and bridge).
"""
from typing import Optional

# Claude Code's `--effort <level>` (claude --help, 2.1.x): low, medium, high, xhigh, max.
# `auto` clears the saved level and returns to the model's default (same page).
EFFORT_LEVELS = {"claude-code": ("low", "medium", "high", "xhigh", "max", "auto")}
COMMANDS = ("compact", "effort")


def effort_levels(provider: Optional[str]):
    return EFFORT_LEVELS.get(provider or "", ())


def command_text(command: str, level: Optional[str] = None, provider: str = "claude-code") -> str:
    """The exact line typed into the pane; ValueError for anything else."""
    if command == "compact" and level is None:
        return "/compact"
    if command == "effort" and level in effort_levels(provider):
        return f"/effort {level}"
    raise ValueError(f"Not a fixed session command: {command!r} {level!r}")
