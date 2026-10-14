"""The Claude Code hook: event mapping, silence and safety."""
import json
import subprocess
import sys
from pathlib import Path

from hooks.claude_code import build_state

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "claude_code.py"


def test_prompt_with_placet_request_acks_it():
    event = {"hook_event_name": "UserPromptSubmit", "prompt": "[Placet Request #42 da claude]: analizza"}
    assert build_state(event) == {"state": "busy", "request_id": 42}


def test_ordinary_prompt_is_just_busy():
    assert build_state({"hook_event_name": "UserPromptSubmit", "prompt": "ciao"}) == {"state": "busy"}


def test_stop_sends_last_message():
    event = {"hook_event_name": "Stop", "last_assistant_message": "fatto"}
    assert build_state(event) == {"state": "idle", "last_message": "fatto"}


def test_other_events_ignored():
    assert build_state({"hook_event_name": "PreToolUse"}) is None


def _run(env, stdin):
    return subprocess.run([sys.executable, str(HOOK)], input=stdin, capture_output=True,
                          text=True, env=env, timeout=10)


def test_inactive_without_agent_env():
    res = _run({"PATH": "/usr/bin"}, json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": "x"}))
    assert res.returncode == 0 and res.stdout == ""


def test_never_fails_or_prints_even_if_backend_is_down():
    env = {"PATH": "/usr/bin", "PLACET_HOOK_AGENT": "claude", "PLACET_API_TOKEN": "t",
           "PLACET_API_URL": "http://127.0.0.1:9"}
    for stdin in ["not json", json.dumps({"hook_event_name": "Stop", "last_assistant_message": "x"})]:
        res = _run(env, stdin)
        assert res.returncode == 0 and res.stdout == ""
