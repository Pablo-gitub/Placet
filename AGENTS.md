---
agents:
  claude:
    name: "Claude Code"
    tmux_session: "agent-claude"
    token: "${PLACET_TOKEN_CLAUDE}"
    allowed_targets: ["claude", "gemini", "codex", "human"]
    default_timeout_seconds: 180
    # Empty prompt, including the dim Try suggestion (calibrated on Claude Code 2.1)
    idle_patterns: ['^\s*[│|]?\s*[>❯]\s*(Try ".*")?\s*[│|]?\s*$']
  gemini:
    name: "Gemini CLI / Antigravity"
    tmux_session: "agent-gemini"
    token: "${PLACET_TOKEN_GEMINI}"
    allowed_targets: ["claude", "gemini", "codex", "human"]
    default_timeout_seconds: 180
    # Calibrate with: python -m bridge.calibrate gemini
    idle_patterns: ['^\s*[│|]?\s*>\s*(Type your message.*)?[│|]?\s*$']
  codex:
    name: "OpenAI Codex / ChatGPT CLI"
    tmux_session: "agent-codex"
    token: "${PLACET_TOKEN_CODEX}"
    allowed_targets: ["claude", "gemini", "codex", "human"]
    default_timeout_seconds: 180
    # Calibrate with: python -m bridge.calibrate codex
    idle_patterns: ['^\s*›\s*$']

rules:
  claude -> claude:
    require_approval: true
  claude -> gemini:
    require_approval: true
  gemini -> claude:
    require_approval: true
  claude -> codex:
    require_approval: true
  codex -> claude:
    require_approval: true
  gemini -> codex:
    require_approval: true
  codex -> gemini:
    require_approval: true
  gemini -> gemini:
    require_approval: true
  codex -> codex:
    require_approval: true

---

# Placet agent registry

This file defines the agents known to Placet, authentication environment references, routing rules and role descriptions. The backend reads it at startup; agents discover it through `placet_list_agents()`. Preserve the YAML frontmatter schema. Production uses `/etc/placet/AGENTS.md`, which ordinary deploys do not overwrite.

## 1. Claude Code (`claude`)

- **Primary role:** software architecture, refactoring, orchestration and tested implementation.
- **Provider:** Anthropic, using the model configured in Claude Code.
- **tmux session:** `agent-claude`.
- **Consult for:** detailed implementation, code review, design patterns and complex refactoring.

## 2. Gemini / Antigravity (`gemini`)

- **Primary role:** long-context reasoning, document/standards analysis, edge-case validation and algorithmic support.
- **Provider:** Google, using the model configured in Gemini CLI.
- **tmux session:** `agent-gemini`.
- **Consult for:** extensive documentation checks, comparisons and context-intensive tasks.

## 3. OpenAI Codex / ChatGPT (`codex`)

- **Primary role:** scripts, rapid prototypes, data transformations and focused synthesis.
- **Provider:** OpenAI, using the model configured in Codex.
- **tmux session:** `agent-codex`.
- **Consult for:** scripting, rapid generation and cross-validation.

## 4. Human supervisor (`human`)

- **Role:** the human supervisor.
- **Channel:** phone ntfy notifications and the PWA over Tailscale.
- **Authority:** final approval of external actions and inter-agent prompts unless an explicit routing policy permits automatic delivery. Collaboration replies also require return approval unless automatic return was explicitly authorized for that request.
