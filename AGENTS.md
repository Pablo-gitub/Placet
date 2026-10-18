---
agents:
  claude:
    name: "Claude Code"
    tmux_session: "agent-claude"
    token: "${PLACET_TOKEN_CLAUDE}"
    allowed_targets: ["claude", "gemini", "codex", "human"]
    default_timeout_seconds: 180
    # Prompt vuoto, anche con il suggerimento grigio 'Try "..."' (calibrato su Claude Code 2.1)
    idle_patterns: ['^\s*[│|]?\s*[>❯]\s*(Try ".*")?\s*[│|]?\s*$']
  gemini:
    name: "Gemini CLI / Antigravity"
    tmux_session: "agent-gemini"
    token: "${PLACET_TOKEN_GEMINI}"
    allowed_targets: ["claude", "gemini", "codex", "human"]
    default_timeout_seconds: 180
    # Da calibrare con: python -m bridge.calibrate gemini
    idle_patterns: ['^\s*[│|]?\s*>\s*(Type your message.*)?[│|]?\s*$']
  codex:
    name: "OpenAI Codex / ChatGPT CLI"
    tmux_session: "agent-codex"
    token: "${PLACET_TOKEN_CODEX}"
    allowed_targets: ["claude", "gemini", "codex", "human"]
    default_timeout_seconds: 180
    # Da calibrare con: python -m bridge.calibrate codex
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

# Registro Agenti Placet

Questo file definisce le sessioni attive conosciute da Placet, le credenziali di autenticazione, le regole di routing e la descrizione qualitativa dei ruoli. Viene letto dal backend all'avvio e interrogato dagli agenti tramite il tool MCP `placet_list_agents()`.

## 1. Claude Code (`claude`)
- **Ruolo Primario:** Architettura software, refactoring, orchestrazione e redazione di codice testato.
- **Provider:** Anthropic (modello configurato nella CLI Claude Code).
- **Sessione tmux:** `agent-claude`
- **Quando consultarlo:** Per implementazione dettagliata, review di codice, design pattern e refactoring complessi.

## 2. Gemini / Antigravity (`gemini`)
- **Ruolo Primario:** Ragionamento su contesti estesi, analisi documentale e standard ufficiali, validazione edge cases e supporto algoritmico.
- **Provider:** Google (modello configurato nella Gemini CLI).
- **Sessione tmux:** `agent-gemini`
- **Quando consultarlo:** Per verifiche su ampia documentazione, analisi comparative o compiti ad alta intensità contestuale.

## 3. OpenAI Codex / ChatGPT (`codex`)
- **Ruolo Primario:** Scrittura di script, prototipazione rapida, data transformations e compiti mirati di sintesi.
- **Provider:** OpenAI (modello configurato nella CLI Codex).
- **Sessione tmux:** `agent-codex`
- **Quando consultarlo:** Per generazioni veloci, compiti di scripting mirati e cross-validation.

## 4. Human Supervisor (`human`)
- **Ruolo:** Paolo (Supervisore Umano).
- **Canale:** iPhone via notifica push ntfy e interfaccia web PWA su Tailscale.
- **Autorità:** Approvazione finale obbligatoria per azioni esterne e revisione prompt tra agenti (salvo regole di auto-routing abilitate).
