# Placet 🕊️

> **Punto unico di supervisione umana e bus di comunicazione cross-sessione per agenti AI.**

Placet permette ad agenti attivi su terminali diversi (Claude Code, Gemini CLI / Antigravity, OpenAI Codex) di collaborare scambiandosi compiti e prompt, offrendo all'utente un punto di controllo su iPhone (via Tailscale e notifiche push `ntfy`) per rivedere, modificare e approvare ogni prompt prima che venga eseguito.

---

## 🏗️ Architettura

```text
 ┌────────────────────────────────────────────────────────┐
 │                    iPhone (Paolo)                      │
 │  • Notifiche push ntfy con bottoni Approva / Rifiuta   │
 │  • PWA (Tailscale HTTPS): revisione ed edit prompt     │
 └───────────────────────────▲────────────────────────────┘
                             │ REST API / SSE
 ┌───────────────────────────┴────────────────────────────┐
 │                Placet Backend (FastAPI)                │
 │  • SQLite: richieste, risposte, audit log              │
 │  • Regole di routing e catalogo da AGENTS.md           │
 └──────────────┬──────────────────────────┬──────────────┘
                │                          │
                ▼                          ▼
     ┌──────────────────────┐   ┌──────────────────────┐
     │  Server MCP Placet   │   │ Local Bridge Daemon  │
     │  (stdio per agenti)  │   │ (tmux send-keys)     │
     └──────────▲───────────┘   └──────────▲───────────┘
                │                          │
        ┌───────┴───────┐          ┌───────┴───────┐
        ▼               ▼          ▼               ▼
   [agent-claude]  [agent-gemini] [agent-claude]  [agent-gemini]
    Claude Code     Gemini CLI     Claude Code     Gemini CLI
```

---

## 🚀 Avvio Rapido

### 1. Prerequisiti
- Python 3.11+
- Node.js 18+ (per build della PWA)
- `tmux` installato sul sistema

### 2. Installazione
```bash
# Setup ambiente virtuale Python
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Build PWA
cd frontend
npm install
npm run build
cd ..
```

### 3. Avvio di Placet (All-in-One)
```bash
# Avvia contemporaneamente il Backend FastAPI (porta 8000) e il Bridge Locale
.venv/bin/python run_all.py
```
La PWA sarà immediatamente accessibile su `http://localhost:8000` (o sul tuo IP Tailscale).

---

## 🤖 Configurazione degli Agenti

### 1. Avvia le sessioni tmux
Crea le sessioni tmux con i nomi convenzionati definiti in `AGENTS.md`:
```bash
tmux new -s agent-claude
tmux new -s agent-gemini
```

### 2. Configura il Server MCP di Placet
Aggiungi Placet alla configurazione MCP del tuo client (es. `claude_desktop_config.json`, `.claude.json` o configurazione Antigravity):

```json
{
  "mcpServers": {
    "placet": {
      "command": "/home/paolo/Projects/placet/.venv/bin/python",
      "args": ["-m", "mcp_server.server"],
      "env": {
        "PLACET_BASE_URL": "http://localhost:8000",
        "PLACET_AGENT_ID": "claude",
        "PLACET_API_TOKEN": "secret-token-claude"
      }
    }
  }
}
```

---

## 🛠️ Strumenti MCP Disponibili

1. `placet_ask_human(question, options, context, timeout_seconds)`
   - Richiede una decisione/scelta all'utente umano su iPhone.
2. `placet_send_to_agent(target_agent, prompt, context, require_human_approval, timeout_seconds)`
   - Invia un compito a un'altra sessione tmux registrata (es. `gemini`).
3. `placet_reply(request_id, response)`
   - Restituisce la risposta generata al completamento del task.
4. `placet_list_agents()`
   - Esplora gli agenti attivi, i loro ruoli e le regole di routing.
5. `placet_check_status(request_id)`
   - Interroga lo stato di una richiesta rimasta in sospeso oltre il timeout.

---

## 🧪 Esecuzione dei Test (TDD)

```bash
PYTHONPATH=. .venv/bin/pytest -v
```
Include suite completa:
- Unit test configurazione e parsing `AGENTS.md`
- Test database SQLite e transizioni di stato
- Integration test API REST, SSE e notifiche
- Test rilevamento idle terminale e bridge tmux
- Test strumenti Server MCP
- End-to-end test del ciclo completo
