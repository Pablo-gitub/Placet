"""
RF-27: the hooks report whether the provider's own configuration lists the
Placet MCP server. Read-only, presence of the entry only.
"""
import json

import pytest

from hooks.claude_code import build_event
from hooks.mcp_check import mcp_state


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


@pytest.fixture
def home(tmp_path):
    return tmp_path / "home"


@pytest.fixture
def project(tmp_path):
    folder = tmp_path / "work" / "repo"
    folder.mkdir(parents=True)
    return folder


def test_claude_global_entry_is_configured(home, project):
    write(home / ".claude.json", {"mcpServers": {"placet": {"command": "python", "env": {"PLACET_API_TOKEN": "s3cret"}}}})
    assert mcp_state("claude-code", str(project), home=home) == "configured"


def test_claude_project_entry_in_dot_claude_json(home, project):
    write(home / ".claude.json", {"mcpServers": {}, "projects": {str(project): {"mcpServers": {"placet": {}}}}})
    assert mcp_state("claude-code", str(project), home=home) == "configured"
    write(home / ".claude.json", {"mcpServers": {}})
    assert mcp_state("claude-code", str(project), home=home) == "missing"


def test_a_project_mcp_json_entry_counts_only_once_approved(home, project):
    """Servers from .mcp.json run only after the user approved them (per project, in ~/.claude.json)."""
    write(project.parent / ".mcp.json", {"mcpServers": {"placet": {}}})   # a folder containing the project
    write(home / ".claude.json", {"mcpServers": {}})
    assert mcp_state("claude-code", str(project), home=home) == "unknown"            # not approved yet
    write(home / ".claude.json", {"projects": {str(project): {"enabledMcpjsonServers": ["placet"]}}})
    assert mcp_state("claude-code", str(project), home=home) == "configured"
    write(home / ".claude.json", {"projects": {str(project): {"enableAllProjectMcpServers": True}}})
    assert mcp_state("claude-code", str(project), home=home) == "configured"
    write(home / ".claude.json", {"projects": {str(project): {"disabledMcpjsonServers": ["placet"],
                                                              "enabledMcpjsonServers": ["github"]}}})
    assert mcp_state("claude-code", str(project), home=home) == "unknown"            # refused by the user
    write(home / ".claude.json", {"projects": {str(project): {"enabledMcpjsonServers": ["github"]}}})
    assert mcp_state("claude-code", str(project), home=home) == "unknown"            # approved others only
    (home / ".claude.json").unlink()
    assert mcp_state("claude-code", str(project), home=home) == "unknown"            # approvals unreadable


def test_an_off_anywhere_wins_over_a_found_entry(home, project):
    write(home / ".claude.json", {"mcpServers": {"placet": {}},
                                  "projects": {str(project): {"disabledMcpjsonServers": ["placet"]}}})
    write(project / ".mcp.json", {"mcpServers": {"placet": {}}})
    assert mcp_state("claude-code", str(project), home=home) == "unknown"
    write(home / ".claude.json", {"mcpServers": {"placet": {}}})                     # the .mcp.json copy is unapproved
    assert mcp_state("claude-code", str(project), home=home) == "unknown"
    (project / ".mcp.json").unlink()
    assert mcp_state("claude-code", str(project), home=home) == "configured"


def test_a_key_that_claude_code_does_not_write_changes_nothing(home, project):
    """`disabledMcpServers` is not a key of the real ~/.claude.json: it must not switch an entry off."""
    write(home / ".claude.json", {"mcpServers": {"placet": {}},
                                  "projects": {str(project): {"disabledMcpServers": ["placet"]}}})
    assert mcp_state("claude-code", str(project), home=home) == "configured"


def test_claude_with_other_servers_only_is_missing(home, project):
    write(home / ".claude.json", {"mcpServers": {"github": {}}})
    assert mcp_state("claude-code", str(project), home=home) == "missing"


def test_nothing_readable_is_unknown_not_missing(home, project):
    assert mcp_state("claude-code", str(project), home=home) == "unknown"
    write(home / ".claude.json", "{not json")
    assert mcp_state("claude-code", str(project), home=home) == "unknown"
    assert mcp_state("claude-code", None, home=home) == "unknown"


def test_codex_reads_the_toml_table(home, project):
    write(home / ".codex" / "config.toml", '[mcp_servers.placet]\ncommand = "python"\n')
    assert mcp_state("codex", str(project), home=home) == "configured"
    write(home / ".codex" / "config.toml", '[mcp_servers.other]\ncommand = "x"\n')
    assert mcp_state("codex", str(project), home=home) == "missing"
    write(home / ".codex" / "config.toml", "= broken")
    assert mcp_state("codex", str(project), home=home) == "unknown"


def test_codex_home_variable_moves_the_file(home, project, tmp_path):
    other = tmp_path / "elsewhere"
    write(other / "config.toml", "[mcp_servers.placet]\ncommand = 'python'\n")
    assert mcp_state("codex", str(project), home=home, env={"CODEX_HOME": str(other)}) == "configured"


def test_antigravity_reads_the_gemini_config_not_the_old_path(home, project):
    write(home / ".gemini" / "antigravity" / "mcp_config.json", {"mcpServers": {"placet": {}}})  # not read by the app
    assert mcp_state("antigravity", str(project), home=home) == "unknown"
    write(home / ".gemini" / "config" / "mcp_config.json", {"mcpServers": {"placet": {}}})
    assert mcp_state("antigravity", str(project), home=home) == "configured"
    write(home / ".gemini" / "config" / "mcp_config.json", {"mcpServers": {}})
    assert mcp_state("antigravity", str(project), home=home) == "missing"


def test_unknown_providers_are_unknown(home, project):
    assert mcp_state("something-else", str(project), home=home) == "unknown"


def test_the_session_start_event_carries_the_state(home, project, monkeypatch):
    write(home / ".claude.json", {"mcpServers": {"github": {}}})
    monkeypatch.setattr("hooks.claude_code.Path.home", lambda: home)
    start = build_event({"hook_event_name": "SessionStart", "cwd": str(project), "source": "startup"})
    assert start["mcp"] == "missing"
    prompt = build_event({"hook_event_name": "UserPromptSubmit", "cwd": str(project), "prompt": "ciao"})
    assert "mcp" not in prompt
    codex = build_event({"hook_event_name": "SessionStart", "cwd": str(project)}, provider="codex")
    assert codex["mcp"] == "unknown"  # no ~/.codex/config.toml there


def test_antigravity_start_events_carry_the_state_but_other_events_do_not(home, project):
    from hooks.antigravity import with_mcp_state
    write(home / ".gemini" / "config" / "mcp_config.json", {"mcpServers": {"placet": {}}})
    start = with_mcp_state({"event": "start", "cwd": str(project)}, home=home)
    assert start["mcp"] == "configured"
    stop = with_mcp_state({"event": "stop", "cwd": str(project)}, home=home)
    assert "mcp" not in stop


def test_a_switched_off_entry_is_not_called_configured_or_missing(home, project):
    write(home / ".claude.json", {"mcpServers": {"placet": {"command": "x", "disabled": True}}})
    assert mcp_state("claude-code", str(project), home=home) == "unknown"
    write(home / ".codex" / "config.toml", "[mcp_servers.placet]\ncommand = 'python'\nenabled = false\n")
    assert mcp_state("codex", str(project), home=home) == "unknown"
    write(home / ".codex" / "config.toml", "[mcp_servers.placet]\ncommand = 'python'\nenabled = true\n")
    assert mcp_state("codex", str(project), home=home) == "configured"
