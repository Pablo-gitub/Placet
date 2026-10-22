"""RF-62: types a fixed /compact or /effort line into the agent's configured tmux pane."""
import logging
from typing import Optional

from backend.app.session_commands import command_text

logger = logging.getLogger(__name__)

# Someone typed in the pane this recently: do not add to their line.
RECENT_INPUT_SECONDS = 15.0


class CommandError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


def run_command(tmux, spec, tmux_session: Optional[str], command: str, level: Optional[str]) -> str:
    """
    Sends the command to `spec`'s pane and returns the line typed. Only the
    pane configured for the agent (never one a message names), only when it
    exists, is idle and nobody is typing; CommandError otherwise.
    """
    if spec is None or tmux_session != spec.name:
        raise CommandError("wrong_session", "Not the tmux session configured for this agent.")
    try:
        line = command_text(command, level)
    except ValueError:
        raise CommandError("invalid", "Not a fixed session command.") from None
    if not tmux.session_exists(spec.name):
        raise CommandError("tmux_missing", f"tmux session {spec.name} is not running.")
    if not tmux.is_session_idle(spec.name, spec.idle_patterns, spec.busy_patterns):
        raise CommandError("not_idle", "The session is not at an empty prompt.")
    recent = tmux.seconds_since_input(spec.name)
    if isinstance(recent, (int, float)) and recent < RECENT_INPUT_SECONDS:
        raise CommandError("user_typing", "Someone is typing in the session.")
    if not tmux.send_keys(spec.name, line, enter=True):
        raise CommandError("send_failed", "tmux refused the keys.")
    return line
