"""
Idle detection must be conservative: injecting into a busy session, over the
user's typing, or into a menu (where the final Enter would confirm an option)
is worse than waiting.
"""
from unittest.mock import patch

import pytest

from backend.app.config import load_agents_config
from bridge.tmux_controller import TmuxController

CLAUDE = load_agents_config().get_agent("claude").idle_patterns
CODEX = load_agents_config().get_agent("codex").idle_patterns

CLAUDE_IDLE = """\
╭───────────────────────────────────────────╮
│ ✻ Welcome to Claude Code!                 │
╰───────────────────────────────────────────╯

────────────────────────────────────────────────
>
────────────────────────────────────────────────
  ? for shortcuts
"""

# Captured from the real Claude Code 2.1 idle prompt: a dimmed suggestion.
CLAUDE_IDLE_SUGGESTION = """\
    ▎ https://code.claude.com/docs/en/permission-mode
      +2 more · /status
   ──────────────────────────────────────────────────
   ❯ Try "refactor client_api.py"
   ──────────────────────────────────────────────────
     ⏵⏵ auto mode on (shift+tab to cycle) · ← for a…
"""

# Captured from the real Claude Code login screen.
CLAUDE_LOGIN = """\
   &state=GYAKz287QnHB3kR4HGmwdYpEBgaxRl03uQCwM1HLzZM
    Paste code here if prompted >
"""

CLAUDE_WORKING = """\
> Analizza il modulo
● Reading backend/app/main.py…
✻ Thinking… (12s · esc to interrupt)
────────────────────────────────────────────────
>
────────────────────────────────────────────────
"""

CLAUDE_TYPING = """\
────────────────────────────────────────────────
> sto scrivendo una domanda
────────────────────────────────────────────────
  ? for shortcuts
"""

# Captured from the real Claude Code first-run screen: a menu, not a prompt.
CLAUDE_THEME_MENU = """\
 To change this later, run /theme
   1. Auto (match terminal)
 ❯ 2. Dark mode ✔
   3. Light mode
   4. Dark mode (colorblind-friendly)
  Syntax theme: Monokai Extended (ctrl+t to disable)
"""

# Captured from the real Codex trust dialog.
CODEX_TRUST_DIALOG = """\
> You are in /home/paolo/Projects/placet
  Do you trust the contents of this directory?
› 1. Yes, continue
  2. No, quit
  Press enter to continue
"""

CODEX_IDLE = """\
>_ OpenAI Codex
›
  ⏎ send   ⇧⏎ newline   ⌃C quit
"""


def _controller(*screens):
    controller = TmuxController(stability_seconds=0)
    mock = patch.object(controller, "capture_pane", side_effect=list(screens))
    return controller, mock


@pytest.mark.parametrize("screen, patterns, idle", [
    (CLAUDE_IDLE, CLAUDE, True),
    (CLAUDE_IDLE_SUGGESTION, CLAUDE, True),
    (CLAUDE_LOGIN, CLAUDE, False),
    (CLAUDE_WORKING, CLAUDE, False),
    (CLAUDE_TYPING, CLAUDE, False),
    (CLAUDE_THEME_MENU, CLAUDE, False),
    (CODEX_IDLE, CODEX, True),
    (CODEX_TRUST_DIALOG, CODEX, False),
])
def test_agent_screens(screen, patterns, idle):
    controller, mock = _controller(screen)
    with mock:
        assert controller.is_session_idle("s", patterns) is idle


def test_busy_marker_reported():
    controller, mock = _controller(CLAUDE_WORKING)
    with mock:
        check = controller.check_idle("s", CLAUDE)
    assert check.busy_marker == "esc to interrupt"


def test_custom_busy_pattern():
    controller, mock = _controller(CLAUDE_IDLE)
    with mock:
        assert not controller.is_session_idle("s", CLAUDE, busy_patterns=["Welcome"])


def test_changing_screen_is_busy():
    controller = TmuxController(stability_seconds=0.01)
    streaming = CLAUDE_IDLE.replace("? for shortcuts", "? for shortcuts.")
    with patch.object(controller, "capture_pane", side_effect=[CLAUDE_IDLE, streaming]):
        check = controller.check_idle("s", CLAUDE)
    assert not check.idle
    assert "cambiando" in check.reason


def test_stable_screen_is_read_twice():
    controller = TmuxController(stability_seconds=0.01)
    with patch.object(controller, "capture_pane", side_effect=[CLAUDE_IDLE, CLAUDE_IDLE]) as cap:
        assert controller.is_session_idle("s", CLAUDE)
    assert cap.call_count == 2


def test_plain_shell_fallback_uses_last_line_only():
    controller, mock = _controller("$ ls\nfile.txt\nuser@host:~$ ")
    with mock:
        assert controller.is_session_idle("s")
    controller, mock = _controller("user@host:~$ ls\nfile.txt")
    with mock:
        assert not controller.is_session_idle("s")


def test_ansi_and_osc_sequences_are_stripped():
    colored = "\x1b]0;title\x07\x1b[1;32m> \x1b[0m"
    controller, mock = _controller(f"────\n{colored}\n────")
    with mock:
        assert controller.is_session_idle("s", CLAUDE)
