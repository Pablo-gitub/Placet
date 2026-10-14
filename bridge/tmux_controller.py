import re
import subprocess
import logging
import time
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

logger = logging.getLogger(__name__)

ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07")
SGR = re.compile(r"\x1b\[([0-9;]*)m")

# Plain shells: the last line is a prompt ending in $, %, #, > or ❯.
SHELL_PROMPT_PATTERNS = [r"[\$%❯>#]\s*$"]

# Agent TUIs show an interrupt hint while they work; any of these on screen
# means busy. Matched case-insensitively as substrings.
DEFAULT_BUSY_PATTERNS = [
    "esc to interrupt",
    "esc to cancel",
    "ctrl+c to interrupt",
]


@dataclass
class IdleCheck:
    """Outcome of an idle check, with the reasons (used by calibration)."""
    idle: bool
    reason: str
    screen: str = ""
    matched_line: Optional[str] = None
    busy_marker: Optional[str] = None


@dataclass
class SessionSpec:
    """How to recognise an idle session (from AGENTS.md)."""
    name: str
    idle_patterns: List[str] = field(default_factory=list)
    busy_patterns: List[str] = field(default_factory=list)
    # hook | mcp | none: how the agent acknowledges an injected prompt.
    ack_mode: str = "mcp"


class TmuxController:
    """
    Manages terminal interaction with named tmux sessions.
    Provides session listing, idle detection via capture-pane, and key injection.

    Idle detection is deliberately conservative: injecting into a session that
    is not at an empty input prompt can corrupt what the user is typing or,
    worse, the final Enter can confirm a menu or dialog. A session is idle only
    if all of these hold:
      1. no busy marker is on screen;
      2. one of the idle patterns matches one of the last lines (agent TUIs
         keep a status footer under the input box, so not only the last one);
      3. the screen does not change over a short interval (no streaming).
    """

    IDLE_TAIL_LINES = 8

    def __init__(self, stability_seconds: float = 0.5):
        self.stability_seconds = stability_seconds

    def list_sessions(self) -> List[str]:
        try:
            res = subprocess.run(
                ["tmux", "list-sessions", "-F", "#{session_name}"],
                capture_output=True,
                text=True,
                check=True,
            )
            return [line.strip() for line in res.stdout.splitlines() if line.strip()]
        except Exception:
            return []

    def session_exists(self, session_name: str) -> bool:
        sessions = self.list_sessions()
        return session_name in sessions

    def capture_pane(self, session_name: str) -> str:
        """Visible screen of the session's active pane."""
        try:
            res = subprocess.run(
                # -e keeps the formatting: dimmed placeholder text must be
                # told apart from text the user typed.
                ["tmux", "capture-pane", "-p", "-e", "-t", session_name],
                capture_output=True,
                text=True,
                check=True,
            )
            return res.stdout
        except Exception as e:
            logger.warning(f"Error capturing pane for session {session_name}: {e}")
            return ""

    @staticmethod
    def strip_ansi(text: str) -> str:
        """
        Plain text of a captured screen, without the text shown dimmed (SGR 2).
        Agent TUIs render placeholders and suggested next prompts dimmed in
        an empty input box (e.g. Claude Code's "❯ controlla se ha risposto"):
        they are not input, and must not make the prompt look non-empty.
        """
        out = []
        dim = False
        pos = 0
        for match in SGR.finditer(text):
            if not dim:
                out.append(text[pos:match.start()])
            else:
                # Keep line structure even inside dimmed text.
                out.append("\n" * text.count("\n", pos, match.start()))
            params = match.group(1).split(";") if match.group(1) else ["0"]
            i = 0
            while i < len(params):
                param = params[i]
                if param in ("38", "48", "58"):
                    # Extended colour: 38;5;N or 38;2;R;G;B - not attributes.
                    mode = params[i + 1] if i + 1 < len(params) else ""
                    i += 3 if mode == "5" else 5 if mode == "2" else 2
                    continue
                if param in ("", "0", "22"):
                    dim = False
                elif param == "2":
                    dim = True
                i += 1
            pos = match.end()
        out.append(text[pos:] if not dim else "\n" * text.count("\n", pos))
        return ANSI_ESCAPE.sub("", "".join(out))

    def check_idle(
        self,
        session_name: str,
        idle_patterns: Optional[Sequence[str]] = None,
        busy_patterns: Optional[Sequence[str]] = None,
    ) -> IdleCheck:
        screen = self.strip_ansi(self.capture_pane(session_name))
        if not screen.strip():
            return IdleCheck(False, "schermo vuoto o sessione non leggibile", screen)

        lower = screen.lower()
        for marker in list(DEFAULT_BUSY_PATTERNS) + list(busy_patterns or []):
            if marker.lower() in lower:
                return IdleCheck(False, f"indicatore di lavoro: {marker!r}", screen, busy_marker=marker)

        lines = [line.rstrip() for line in screen.splitlines() if line.strip()]
        if idle_patterns:
            candidates = lines[-self.IDLE_TAIL_LINES:]
            patterns = list(idle_patterns)
        else:
            candidates = lines[-1:]
            patterns = SHELL_PROMPT_PATTERNS
        matched = next(
            (line for line in reversed(candidates) if any(re.search(p, line) for p in patterns)),
            None,
        )
        if matched is None:
            return IdleCheck(False, "nessun prompt di input vuoto riconosciuto", screen)

        if self.stability_seconds > 0:
            time.sleep(self.stability_seconds)
            again = self.strip_ansi(self.capture_pane(session_name))
            if again != screen:
                return IdleCheck(False, "lo schermo sta cambiando", screen, matched_line=matched)

        return IdleCheck(True, "prompt vuoto e schermo stabile", screen, matched_line=matched)

    def is_session_idle(
        self,
        session_name: str,
        idle_patterns: Optional[Sequence[str]] = None,
        busy_patterns: Optional[Sequence[str]] = None,
    ) -> bool:
        return self.check_idle(session_name, idle_patterns, busy_patterns).idle

    def seconds_since_input(self, session_name: str) -> Optional[float]:
        """
        Seconds since a client attached to the session last sent input
        (a key press), or None if no client is attached.
        """
        try:
            res = subprocess.run(
                ["tmux", "list-clients", "-t", session_name, "-F", "#{client_activity}"],
                capture_output=True,
                text=True,
                check=True,
            )
        except Exception:
            return None
        stamps = [int(v) for v in res.stdout.split() if v.strip().isdigit()]
        if not stamps:
            return None
        return max(0.0, time.time() - max(stamps))

    def send_keys(self, session_name: str, text: str, enter: bool = True) -> bool:
        """
        Injects text into tmux session.
        For multiline text, uses tmux load-buffer + paste-buffer to avoid line truncation
        or bracketed paste issues. For single-line text, uses send-keys -l (literal).
        """
        try:
            if "\n" in text:
                subprocess.run(
                    ["tmux", "load-buffer", "-"],
                    input=text.encode("utf-8") if isinstance(text, str) else text,
                    check=True,
                )
                subprocess.run(
                    ["tmux", "paste-buffer", "-p", "-t", session_name],
                    check=True,
                )
            else:
                subprocess.run(
                    ["tmux", "send-keys", "-t", session_name, "-l", text],
                    check=True,
                )

            if enter:
                subprocess.run(
                    ["tmux", "send-keys", "-t", session_name, "Enter"],
                    check=True,
                )
            return True
        except Exception as e:
            logger.error(f"Failed to send keys to tmux session {session_name}: {e}")
            return False
