"""
RF-27: a Claude Code session whose configuration lacks the Placet MCP server
is told so at start, in additionalContext, so it can tell the user.
"""
import io
import json

import pytest

from hooks import claude_code
from hooks.claude_code import startup_context


def test_only_a_missing_server_on_claude_code_gets_a_message():
    ctx = startup_context({"event": "start", "mcp": "missing", "provider": "claude-code"})
    out = ctx["hookSpecificOutput"]
    assert out["hookEventName"] == "SessionStart"
    assert "placet_reply" in out["additionalContext"] and "tell the user" in out["additionalContext"].lower()
    for mcp in ("configured", "unknown", None):
        assert startup_context({"event": "start", "mcp": mcp, "provider": "claude-code"}) is None
    assert startup_context({"event": "start", "mcp": "missing", "provider": "codex"}) is None
    assert startup_context({"event": "prompt", "mcp": "missing", "provider": "claude-code"}) is None


def run_hook(monkeypatch, capsys, tmp_path, config):
    home = tmp_path / "home"
    home.mkdir()
    if config is not None:
        (home / ".claude.json").write_text(json.dumps(config))
    sent = []
    monkeypatch.setattr(claude_code.Path, "home", lambda: home)
    monkeypatch.setattr(claude_code.spool, "write_event", lambda agent, sid, body: sent.append(body))
    monkeypatch.setattr("sys.argv", ["claude_code.py", "--agent", "claude"])
    event = {"hook_event_name": "SessionStart", "session_id": "s1", "cwd": str(tmp_path), "source": "startup"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    claude_code.main()
    return capsys.readouterr().out, sent


def test_the_hook_prints_the_context_only_when_the_server_is_missing(monkeypatch, capsys, tmp_path):
    out, sent = run_hook(monkeypatch, capsys, tmp_path, {"mcpServers": {"github": {}}})
    assert json.loads(out)["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert sent[0]["mcp"] == "missing"  # the event still goes to Placet


@pytest.mark.parametrize("config", [{"mcpServers": {"placet": {}}}, None])
def test_the_hook_stays_silent_otherwise(monkeypatch, capsys, tmp_path, config):
    out, sent = run_hook(monkeypatch, capsys, tmp_path, config)
    assert out == "" and sent
