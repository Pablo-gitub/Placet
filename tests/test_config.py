import os
import tempfile
import pytest
from backend.app.config import load_agents_config, PlacetConfig

SAMPLE_AGENTS_MD = """---
agents:
  claude:
    name: "Claude Code"
    tmux_session: "agent-claude"
    token: "secret-token-claude"
    allowed_targets: ["gemini", "human"]
    default_timeout_seconds: 180
  gemini:
    name: "Gemini CLI"
    tmux_session: "agent-gemini"
    token: "secret-token-gemini"
    allowed_targets: ["claude", "human"]
    default_timeout_seconds: 120

rules:
  claude -> gemini:
    require_approval: true
  gemini -> claude:
    require_approval: false
---

# Registro Agenti
Informazioni descrittive...
"""

def test_load_agents_config_from_file():
    with tempfile.NamedTemporaryFile("w+", suffix=".md", delete=False) as f:
        f.write(SAMPLE_AGENTS_MD)
        f_path = f.name

    try:
        config = load_agents_config(f_path)
        assert isinstance(config, PlacetConfig)
        assert len(config.agents) == 2
        
        # Verify claude agent
        claude = config.agents["claude"]
        assert claude.name == "Claude Code"
        assert claude.tmux_session == "agent-claude"
        assert claude.token == "secret-token-claude"
        assert "gemini" in claude.allowed_targets
        assert claude.default_timeout_seconds == 180

        # Verify gemini agent
        gemini = config.agents["gemini"]
        assert gemini.name == "Gemini CLI"
        assert gemini.default_timeout_seconds == 120

        # Verify rules
        assert config.requires_approval("claude", "gemini") is True
        assert config.requires_approval("gemini", "claude") is False
        # Undefined rule defaults to True for safety
        assert config.requires_approval("claude", "unknown") is True

        # Verify allowed routing
        assert config.is_route_allowed("claude", "gemini") is True
        assert config.is_route_allowed("gemini", "claude") is True
        assert config.is_route_allowed("gemini", "unlisted") is False

        # Verify markdown body was extracted
        assert "Registro Agenti" in config.description_markdown
    finally:
        os.remove(f_path)


def test_env_var_interpolation():
    os.environ["TEST_ENV_TOKEN"] = "my-interpolated-token"
    yaml_content = """---
agents:
  test_agent:
    name: "Test"
    tmux_session: "agent-test"
    token: "${TEST_ENV_TOKEN:-fallback}"
    allowed_targets: ["human"]
rules: {}
---
Desc
"""
    with tempfile.NamedTemporaryFile("w+", suffix=".md", delete=False) as f:
        f.write(yaml_content)
        f_path = f.name

    try:
        config = load_agents_config(f_path)
        assert config.agents["test_agent"].token == "my-interpolated-token"
    finally:
        os.remove(f_path)
        del os.environ["TEST_ENV_TOKEN"]


def test_load_default_agents_md():
    config = load_agents_config()
    assert "claude" in config.agents
    assert "gemini" in config.agents
    assert "codex" in config.agents
    assert config.agents["claude"].tmux_session == "agent-claude"
    assert config.requires_approval("claude", "gemini") is True
    assert config.is_route_allowed("claude", "gemini") is True

