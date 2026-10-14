---
agents:
  claude:
    name: "Claude Code"
    tmux_session: "agent-claude"
    token: "${PLACET_TOKEN_CLAUDE:-secret-token-claude}"
    allowed_targets: ["gemini", "codex", "human"]
    default_timeout_seconds: 180
  gemini:
    name: "Gemini CLI / Antigravity"
    tmux_session: "agent-gemini"
    token: "${PLACET_TOKEN_GEMINI:-secret-token-gemini}"
    allowed_targets: ["claude", "codex", "human"]
    default_timeout_seconds: 180
  codex:
    name: "OpenAI Codex / ChatGPT CLI"
    tmux_session: "agent-codex"
    token: "${PLACET_TOKEN_CODEX:-secret-token-codex}"
    allowed_targets: ["claude", "gemini", "human"]
    default_timeout_seconds: 180

rules:
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
---

# Registro Agenti Placet

Questo file definisce le sessioni attive conosciute da Placet, le credenziali di autenticazione, le regole di routing e la descrizione qualitativa dei ruoli. Viene letto dal backend all'avvio e interrogato dagli agenti tramite il tool MCP `placet_list_agents()`.

## 1. Claude Code (`claude`)
- **Ruolo Primario:** Architettura software, refactoring, orchestrazione e redazione di codice testato.
- **Provider:** Anthropic Claude (Claude 3.7 Sonnet).
- **Sessione tmux:** `agent-claude`
- **Quando consultarlo:** Per implementazione dettagliata, review di codice, design pattern e refactoring complessi.

## 2. Gemini / Antigravity (`gemini`)
- **Ruolo Primario:** Ragionamento su contesti estesi, analisi documentale e standard ufficiali, validazione edge cases e supporto algoritmico.
- **Provider:** Google Gemini.
- **Sessione tmux:** `agent-gemini`
- **Quando consultarlo:** Per verifiche su ampia documentazione, analisi comparative o compiti ad alta intensità contestuale.

## 3. OpenAI Codex / ChatGPT (`codex`)
- **Ruolo Primario:** Scrittura di script, prototipazione rapida, data transformations e compiti mirati di sintesi.
- **Provider:** OpenAI (GPT-4o / o1 / o3-mini).
- **Sessione tmux:** `agent-codex`
- **Quando consultarlo:** Per generazioni veloci, compiti di scripting mirati e cross-validation.

## 4. Human Supervisor (`human`)
- **Ruolo:** Paolo (Supervisore Umano).
- **Canale:** iPhone via notifica push ntfy e interfaccia web PWA su Tailscale.
- **Autorità:** Approvazione finale obbligatoria per azioni esterne e revisione prompt tra agenti (salvo regole di auto-routing abilitate).
