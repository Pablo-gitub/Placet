"""RF-62: types a fixed /compact or /effort line into the pane of a Placet session."""
import logging
import re
import time
from contextlib import nullcontext
from typing import Optional

from backend.app.session_commands import command_text
from bridge.procs import Procs
from bridge.tmux_controller import TmuxCheckError

logger = logging.getLogger(__name__)

# Someone typed in the pane this recently: do not add to their line.
RECENT_INPUT_SECONDS = 15.0
# After Enter, the time a confirmation dialog needs to appear before the screen is read.
SETTLE_SECONDS = 1.0
PANE_ID = re.compile(r"^%\d{1,9}$")


class CommandError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


def _number(value) -> Optional[int]:
    return int(value) if isinstance(value, (int, str)) and str(value).isdigit() else None


def run_command(tmux, spec, tmux_session: Optional[str], tmux_pane: Optional[str],
                command: str, level: Optional[str], lock=None, identity: Optional[dict] = None,
                procs=None):
    """
    Sends the command to the session's pane and returns the line typed. The
    target is the stable pane id (%N), never a session name: a pane switch
    between the checks and the send cannot redirect it. That pane must still
    belong to the tmux session configured for the agent (never one a message
    names), exist, be idle and not be typed in. `lock` serializes this with
    normal deliveries to the same terminal. A pane id is only unique within one
    tmux server and while its process lives, so the pane must also be held by the
    tmux server the session reported, and the session's own agent process (same
    pid and start time) must be alive and run under the pane's process: a tmux
    restart that reused %N, or a ghost session, is refused. Returns (line typed,
    needs_confirmation): after Enter the screen is read once more, still under the
    lock, because /effort may open a confirmation dialog; a screen that is neither
    an idle prompt nor a command in progress is "needs confirmation" (on the PC),
    an unreadable one is CommandError("unverified"). CommandError otherwise.
    """
    identity = identity or {}
    server, agent_pid, agent_started = (_number(identity.get("tmux_server")), _number(identity.get("agent_pid")),
                                        identity.get("agent_started"))
    if server is None or agent_pid is None or not agent_started:
        raise CommandError("identity_unknown", "The session did not report which process runs in its terminal.")
    if spec is None or tmux_session != spec.name:
        raise CommandError("wrong_session", "Not the tmux session configured for this agent.")
    if not isinstance(tmux_pane, str) or not PANE_ID.match(tmux_pane):
        raise CommandError("tmux_missing", "No terminal pane is known for this session.")
    try:
        line = command_text(command, level)
    except ValueError:
        raise CommandError("invalid", "Not a fixed session command.") from None
    with lock if lock is not None else nullcontext():
        info = tmux.pane_info(tmux_pane)
        if info is None:
            raise CommandError("tmux_missing", f"Pane {tmux_pane} is not running.")
        if info.session_name != spec.name:
            raise CommandError("wrong_session", f"Pane {tmux_pane} no longer belongs to {spec.name}.")
        if info.server_pid != server:
            raise CommandError("server_mismatch", "tmux was restarted: this pane id belongs to another server.")
        procs = procs or Procs()
        if not procs.alive(agent_pid, agent_started):
            raise CommandError("agent_gone", "The session's agent process is not running.")
        if not procs.descends(agent_pid, info.pane_pid):
            raise CommandError("agent_not_in_pane", "The session's agent does not run in this pane.")
        if not tmux.is_session_idle(tmux_pane, spec.idle_patterns, spec.busy_patterns):
            raise CommandError("not_idle", "The session is not at an empty prompt.")
        try:
            recent = tmux.input_idle_seconds(spec.name)   # None: no client attached, nobody typing
        except TmuxCheckError:
            raise CommandError("check_failed", "tmux could not tell whether someone is typing.") from None
        if isinstance(recent, (int, float)) and recent < RECENT_INPUT_SECONDS:
            raise CommandError("user_typing", "Someone is typing in the session.")
        # Two steps, so that a failure tells what happened: nothing typed (send_failed,
        # certain) or the text typed but never confirmed (enter_failed, uncertain).
        if not tmux.type_text(tmux_pane, line):
            raise CommandError("send_failed", "tmux refused the keys; nothing was typed.")
        if not tmux.press_enter(tmux_pane):
            raise CommandError("enter_failed", "The text was typed but Enter was not sent: check the session.")
        time.sleep(SETTLE_SECONDS)
        screen = tmux.check_idle(tmux_pane, spec.idle_patterns, spec.busy_patterns)
        if screen.idle or screen.busy_marker or screen.reason.startswith("screen is changing"):
            return line, False          # back at the prompt, or the command is running
        if screen.reason.startswith("empty screen"):
            raise CommandError("unverified", "Enter was sent but the screen cannot be read: check the session.")
        return line, True               # something else is on screen: a dialog waits for the user
