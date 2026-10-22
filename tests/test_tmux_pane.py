"""RF-62: hooks report the tmux pane id ($TMUX_PANE), the stable address of a session's terminal."""
import pytest

from hooks import claude_code


def test_the_pane_id_is_reported_only_inside_tmux_and_only_if_well_formed(monkeypatch):
    monkeypatch.setattr(claude_code, "_tmux_session", lambda: "work")
    event = {"hook_event_name": "SessionStart", "cwd": "/p"}
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,1,0")
    monkeypatch.setenv("TMUX_PANE", "%7")
    assert claude_code.build_event(event)["tmux_pane"] == "%7"
    monkeypatch.setenv("TMUX_PANE", "7; rm -rf")
    assert claude_code.build_event(event).get("tmux_pane") is None
    monkeypatch.setenv("TMUX_PANE", "%7")
    monkeypatch.delenv("TMUX")
    assert claude_code.build_event(event).get("tmux_pane") is None


def test_the_pane_is_on_every_event_like_the_tmux_session(monkeypatch):
    monkeypatch.setattr(claude_code, "_tmux_session", lambda: "work")
    monkeypatch.setenv("TMUX", "x")
    monkeypatch.setenv("TMUX_PANE", "%12")
    for name in ("UserPromptSubmit", "Stop", "SessionEnd"):
        assert claude_code.build_event({"hook_event_name": name, "cwd": "/p", "prompt": "x"})["tmux_pane"] == "%12"
