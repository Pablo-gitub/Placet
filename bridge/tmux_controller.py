import re
import subprocess
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)


class TmuxController:
    """
    Manages terminal interaction with named tmux sessions.
    Provides session listing, idle detection via capture-pane, and key injection.
    """

    IDLE_PROMPT_REGEX = re.compile(r"([\$%❯>#]|claude>)\s*$", re.MULTILINE)
    BUSY_PATTERNS = [
        "generating response",
        "thinking...",
        "[thinking]",
        "compiling",
        "building",
        "running...",
    ]

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

    def capture_pane(self, session_name: str, lines: int = 20) -> str:
        try:
            res = subprocess.run(
                ["tmux", "capture-pane", "-p", "-t", session_name, "-S", f"-{lines}"],
                capture_output=True,
                text=True,
                check=True,
            )
            return res.stdout
        except Exception as e:
            logger.warning(f"Error capturing pane for session {session_name}: {e}")
            return ""

    def is_session_idle(
        self,
        session_name: str,
        custom_idle_patterns: Optional[List[str]] = None,
    ) -> bool:
        """
        Captures the visible output of the pane and determines if the session
        is waiting at an interactive prompt (idle) rather than processing.
        """
        raw_output = self.capture_pane(session_name)
        if not raw_output:
            return False

        # Strip ANSI escape codes
        output = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", raw_output)

        lower_output = output.lower()
        for busy in self.BUSY_PATTERNS:
            if busy in lower_output:
                return False

        lines = [line.strip() for line in output.splitlines() if line.strip()]
        if not lines:
            return False

        last_line = lines[-1]
        if self.IDLE_PROMPT_REGEX.search(last_line):
            return True

        if custom_idle_patterns:
            for pattern in custom_idle_patterns:
                if re.search(pattern, last_line):
                    return True

        return False

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

