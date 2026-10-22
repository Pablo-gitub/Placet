"""RF-62: types a fixed /compact or /effort line into the pane of a Placet session."""
import logging
import re
from contextlib import nullcontext
from typing import Optional

from backend.app.session_commands import command_text

logger = logging.getLogger(__name__)

# Someone typed in the pane this recently: do not add to their line.
RECENT_INPUT_SECONDS = 15.0
PANE_ID = re.compile(r"^%\d{1,9}$")


class CommandError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


def run_command(tmux, spec, tmux_session: Optional[str], tmux_pane: Optional[str],
                command: str, level: Optional[str], lock=None) -> str:
    """
    Sends the command to the session's pane and returns the line typed. The
    target is the stable pane id (%N), never a session name: a pane switch
    between the checks and the send cannot redirect it. That pane must still
    belong to the tmux session configured for the agent (never one a message
    names), exist, be idle and not be typed in. `lock` serializes this with
    normal deliveries to the same terminal. CommandError otherwise.
    """
    if spec is None or tmux_session != spec.name:
        raise CommandError("wrong_session", "Not the tmux session configured for this agent.")
    if not isinstance(tmux_pane, str) or not PANE_ID.match(tmux_pane):
        raise CommandError("tmux_missing", "No terminal pane is known for this session.")
    try:
        line = command_text(command, level)
    except ValueError:
        raise CommandError("invalid", "Not a fixed session command.") from None
    with lock if lock is not None else nullcontext():
        owner = tmux.pane_session_name(tmux_pane)
        if owner is None:
            raise CommandError("tmux_missing", f"Pane {tmux_pane} is not running.")
        if owner != spec.name:
            raise CommandError("wrong_session", f"Pane {tmux_pane} no longer belongs to {spec.name}.")
        if not tmux.is_session_idle(tmux_pane, spec.idle_patterns, spec.busy_patterns):
            raise CommandError("not_idle", "The session is not at an empty prompt.")
        recent = tmux.seconds_since_input(spec.name)
        if isinstance(recent, (int, float)) and recent < RECENT_INPUT_SECONDS:
            raise CommandError("user_typing", "Someone is typing in the session.")
        if not tmux.send_keys(tmux_pane, line, enter=True):
            raise CommandError("send_failed", "tmux refused the keys.")
    return line
