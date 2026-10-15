"""The Claude Code hook: event mapping, silence and safety."""
import json
import subprocess
import sys
from pathlib import Path

from hooks.claude_code import _request_id_from_tool_response, build_event

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "claude_code.py"


def test_session_start_registers():
    payload = build_event({"hook_event_name": "SessionStart", "cwd": "/p"})
    assert payload["event"] == "start" and payload["cwd"] == "/p"
    assert payload["provider"] == "claude-code"


def test_prompt_with_placet_request_acks_it():
    event = {"hook_event_name": "UserPromptSubmit", "prompt": "[Placet Request #42 da claude]: analizza"}
    assert build_event(event)["request_id"] == 42


def test_wake_up_prompt_carries_the_request_id():
    """The hook-channel wake-up arrives as a system reminder inside the prompt."""
    prompt = (
        "<task-notification>\n<summary>Stop hook feedback</summary>\n</task-notification>\n"
        "<system-reminder>\nStop hook blocking error from command \"Stop\": "
        "[Placet Request #7 da gemini]: verifica\n</system-reminder>"
    )
    assert build_event({"hook_event_name": "UserPromptSubmit", "prompt": prompt})["request_id"] == 7


def test_ordinary_prompt_is_just_busy():
    payload = build_event({"hook_event_name": "UserPromptSubmit", "prompt": "ciao", "session_title": "T"})
    assert payload["event"] == "prompt" and "request_id" not in payload and payload["title"] == "T"


def test_stop_sends_last_message():
    payload = build_event({"hook_event_name": "Stop", "last_assistant_message": "fatto"})
    assert payload["event"] == "stop" and payload["last_message"] == "fatto"


def test_placet_tool_call_links_the_request():
    response = [{"type": "text", "text": json.dumps({"id": 9, "sender_id": "claude", "status": "PENDING"})}]
    event = {"hook_event_name": "PostToolUse", "tool_name": "mcp__placet__placet_ask_human",
             "tool_response": response}
    assert build_event(event) == {**build_event(event), "event": "tool", "request_id": 9}


def test_other_tools_and_events_ignored():
    assert build_event({"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_response": "{}"}) is None
    assert build_event({"hook_event_name": "PreToolUse"}) is None


def test_tool_response_shapes():
    body = {"id": 3, "sender_id": "claude"}
    assert _request_id_from_tool_response(json.dumps(body)) == 3
    assert _request_id_from_tool_response(body) == 3
    assert _request_id_from_tool_response([{"type": "text", "text": json.dumps(body)}]) == 3
    assert _request_id_from_tool_response('{"status": "ERROR"}') is None
    assert _request_id_from_tool_response(None) is None


def _run(env, stdin, *args):
    return subprocess.run([sys.executable, str(HOOK), *args], input=stdin, capture_output=True,
                          text=True, env=env, timeout=10)


def test_inactive_without_agent():
    res = _run({"PATH": "/usr/bin"}, json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": "x",
                                                 "session_id": "s"}))
    assert res.returncode == 0 and res.stdout == ""


def test_never_fails_or_prints_even_if_backend_is_down():
    env = {"PATH": "/usr/bin", "PLACET_API_TOKEN": "t", "PLACET_API_URL": "http://127.0.0.1:9"}
    for stdin in ["not json", json.dumps({"hook_event_name": "Stop", "session_id": "s",
                                          "last_assistant_message": "x"})]:
        res = _run(env, stdin, "--agent", "claude")
        assert res.returncode == 0 and res.stdout == ""


def test_tool_response_wrapped_in_content_object():
    body = {"id": 11, "sender_id": "claude"}
    wrapped = {"content": [{"type": "text", "text": json.dumps(body)}], "isError": False}
    assert _request_id_from_tool_response(wrapped) == 11
    assert _request_id_from_tool_response(json.dumps(wrapped)) == 11


def test_tool_response_as_sent_by_the_desktop_app():
    """Captured from the Claude desktop app: {"result": "<request JSON as text>"} as text."""
    inner = json.dumps({"id": 5, "sender_id": "claude", "status": "PENDING"}, indent=2)
    captured = json.dumps({"result": inner})
    assert _request_id_from_tool_response(captured) == 5
