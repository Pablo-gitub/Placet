"""
RF-62 against real screens: captured from Claude Code 2.1.303 in a disposable tmux
session (October 22, 2026), trimmed of everything personal. They show what the
bridge's checks meet after typing a command: `/effort high` on a cached
conversation opens a confirmation dialog; `/effort auto` applies at once;
`/compact` shows "esc to interrupt" while it runs.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from bridge import session_commands
from bridge.session_commands import run_command
from bridge.tmux_controller import PaneInfo, SessionSpec, TmuxController

ROOT = Path(__file__).resolve().parent.parent
SCREENS = Path(__file__).resolve().parent / "fixtures" / "claude_cli"


def screen(name):
    return (SCREENS / f"{name}.txt").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def spec():
    """The claude entry of AGENTS.md (read only): what the bridge really uses."""
    front = (ROOT / "AGENTS.md").read_text(encoding="utf-8").split("---")[1]
    agent = yaml.safe_load(front)["agents"]["claude"]
    return SessionSpec(name=agent["tmux_session"], idle_patterns=agent["idle_patterns"],
                       busy_patterns=agent.get("busy_patterns") or [])


class ScreenTmux(TmuxController):
    """A real TmuxController whose pane shows `before` until Enter is pressed, then `after`."""

    def __init__(self, spec, before, after):
        super().__init__(stability_seconds=0)
        self.spec, self.before, self.after, self.entered = spec, before, after, False

    def capture_pane(self, target):
        return self.after if self.entered else self.before

    def pane_info(self, pane):
        return PaneInfo(4242, 900, self.spec.name)

    def input_idle_seconds(self, name):
        return None

    def type_text(self, target, text):
        return True

    def press_enter(self, target):
        self.entered = True
        return True


class AliveProcs:
    def alive(self, pid, started):
        return True

    def descends(self, pid, ancestor):
        return True


IDENTITY = {"tmux_server": "4242", "agent_pid": 777, "agent_started": "1"}


def run(spec, monkeypatch, after, command="effort", level="high", before="effort-auto"):
    monkeypatch.setattr(session_commands, "SETTLE_SECONDS", 0)
    tmux = ScreenTmux(spec, screen(before), screen(after))
    return run_command(tmux, spec, spec.name, "%3", command, level, identity=IDENTITY, procs=AliveProcs())


@pytest.mark.parametrize("name", ["start", "effort-auto", "compact-done"])
def test_the_configured_idle_patterns_recognise_the_real_idle_prompt(spec, name):
    """`❯ ` with nothing typed, with or without the dim `Try "..."` suggestion."""
    check = TmuxController(stability_seconds=0)
    check.capture_pane = lambda target: screen(name)
    assert check.check_idle("%3", spec.idle_patterns, spec.busy_patterns).idle


def test_the_effort_dialog_is_not_an_idle_prompt_and_not_busy(spec):
    check = TmuxController(stability_seconds=0)
    check.capture_pane = lambda target: screen("effort-high")
    result = check.check_idle("%3", spec.idle_patterns, spec.busy_patterns)
    assert not result.idle and result.busy_marker is None and result.reason == "no empty input prompt recognized"


def test_the_real_effort_dialog_is_reported_as_needs_confirmation(spec, monkeypatch):
    assert run(spec, monkeypatch, "effort-high") == ("/effort high", True)


def test_the_real_screen_after_effort_auto_is_sent(spec, monkeypatch):
    assert run(spec, monkeypatch, "effort-auto", level="auto") == ("/effort auto", False)


def test_a_running_compact_is_sent_because_of_its_esc_to_interrupt_footer(spec, monkeypatch):
    assert run(spec, monkeypatch, "compact-2s", command="compact", level=None) == ("/compact", False)
    assert run(spec, monkeypatch, "compact-done", command="compact", level=None) == ("/compact", False)
