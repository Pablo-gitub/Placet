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

### 3. Variabili d'ambiente

| Variabile | Significato |
|---|---|
| `PLACET_ALLOWED_USERS` | Login Tailscale autorizzati alla PWA, separati da virgola (es. `paolo@github`). Se vuota, nessuno può usare la PWA |
| `PLACET_TOKEN_CLAUDE`, `PLACET_TOKEN_GEMINI`, `PLACET_TOKEN_CODEX` | Token degli agenti, interpolati in `AGENTS.md`. Un agente senza token non può autenticarsi |
| `PLACET_HOST` / `PLACET_PORT` | Listener TCP per gli agenti (default `127.0.0.1:8000`) |
| `PLACET_HUMAN_SOCKET` | Socket Unix per la PWA (default `$XDG_RUNTIME_DIR/placet/human.sock`) |
| `PLACET_CORS_ORIGINS` | Origini ammesse via CORS, solo per un dev server separato (default: nessuna) |
| `PLACET_DB_PATH` | Percorso del DB SQLite |
| `PLACET_AGENTS_FILE` | Percorso di `AGENTS.md` (in produzione `/etc/placet/AGENTS.md`) |
| `PLACET_BRIDGE_TOKEN` | Token del bridge, ammesso solo sugli endpoint `/api/bridge/*` |
| `PLACET_API_URL` | Per bridge e server MCP: indirizzo del backend (default `http://127.0.0.1:8000`) |
| `PLACET_BASE_URL` | Indirizzo della PWA visto dall'iPhone (es. `https://muletto.tail1234.ts.net`), usato nei link delle notifiche |
| `PLACET_NTFY_URL`, `PLACET_NTFY_TOPIC`, `PLACET_NTFY_TOKEN` | Server ntfy, topic e token di accesso. Senza URL, topic e `PLACET_BASE_URL` le notifiche sono disattivate |

Genera i token con `python -c "import secrets; print(secrets.token_urlsafe(32))"`.

### 4. Avvio di Placet (All-in-One)
```bash
# Avvia il Backend (listener agenti + socket PWA) e il Bridge Locale
.venv/bin/python run_all.py
```

Il backend ha due ingressi:
- **Agenti:** `http://127.0.0.1:8000`, autenticati solo con Bearer token. L'identità dell'agente è quella del token.
- **PWA (tu):** il socket Unix `PLACET_HUMAN_SOCKET`, da esporre sulla tailnet con:
  ```bash
  tailscale serve --bg unix:$XDG_RUNTIME_DIR/placet/human.sock
  ```
  Tailscale Serve aggiunge l'header `Tailscale-User-Login`, accettato **solo** su questo socket e solo per gli utenti in `PLACET_ALLOWED_USERS`.

La PWA è poi raggiungibile su `https://<nome-macchina>.<tailnet>.ts.net`.

`run_all.py` è la modalità di sviluppo: backend e bridge nello stesso processo, con il tuo utente. Gli agenti, che girano anch'essi come te, potrebbero leggere il DB e approvarsi le richieste. Per l'uso reale installa i servizi come descritto sotto.

### 4b. Installazione sul muletto (produzione)

```bash
sudo deploy/install.sh --login tuo-login@github
```

Lo script, che si può rilanciare anche per aggiornare:
- **Codice:** compila la PWA e copia il codice in `/opt/placet`, di proprietà di root. Gli agenti non possono modificare il codice che approva le richieste.
- **Backend:** crea l'utente di sistema `placet` e installa `placet-backend.service`. Il DB sta in `/var/lib/placet` e il socket della PWA in `/run/placet`, entrambi accessibili solo a `placet`.
- **Configurazione:** crea `/etc/placet/AGENTS.md` (da qui in poi modifichi questo, non la copia nel repository) e `/etc/placet/placet.env` con token generati a caso.
- **Bridge:** installa `placet-bridge.service` come servizio del tuo utente. Il bridge parla col backend solo via REST, con il token in `~/.config/placet/bridge.env`, e non ha accesso al DB.
- **Token degli agenti:** li trovi in `~/.config/placet/agent-tokens.env`, da copiare nelle configurazioni MCP.

Poi, una sola volta:
```bash
sudo tailscale serve --bg unix:/run/placet/human.sock
```

**Cosa protegge e cosa no.** Un agente non può approvare richieste, perché l'approvazione arriva solo dal socket riservato a tailscaled. Non può nemmeno modificare codice, regole di routing o DB. Gli agenti però girano tutti come te, quindi possono leggere i token l'uno dell'altro: i token servono a distinguerli nell'audit log, non a proteggerli tra loro. Non aggiungere `paolo` a sudo senza password: annullerebbe l'isolamento.

### 5. Sviluppo del frontend

Il dev server Vite inoltra `/api` al socket della PWA e aggiunge l'header di identità che in produzione mette `tailscale serve`:
```bash
cd frontend
PLACET_DEV_LOGIN=paolo@github npx vite
```
`PLACET_DEV_LOGIN` deve comparire in `PLACET_ALLOWED_USERS`. Il dev server ascolta solo su `127.0.0.1`.

### 6. Notifiche push con ntfy self-hosted

La notifica annuncia la richiesta e apre la PWA. Non ha bottoni Approva/Rifiuta: si decide solo dalla PWA, vedendo il testo completo. Prompt e contesto finiscono nella notifica solo se il server ntfy è privato; con `ntfy.sh` pubblico la notifica dice solo chi ha scritto.

1. Avvia ntfy sul muletto, raggiungibile solo in locale:
   ```bash
   docker run -d --name ntfy --restart unless-stopped \
     -p 127.0.0.1:2586:80 -v ntfy-cache:/var/cache/ntfy -v ntfy-etc:/etc/ntfy \
     -e NTFY_BASE_URL=https://muletto.tail1234.ts.net:8443 \
     -e NTFY_UPSTREAM_BASE_URL=https://ntfy.sh \
     -e NTFY_AUTH_FILE=/etc/ntfy/user.db -e NTFY_AUTH_DEFAULT_ACCESS=deny-all \
     binwiederhier/ntfy serve
   ```
   `NTFY_UPSTREAM_BASE_URL` serve per le notifiche istantanee su iOS: Apple consegna la push tramite ntfy.sh, che riceve solo un ID messaggio senza contenuto. L'app sull'iPhone scarica poi il messaggio dal tuo server via Tailscale.
2. Crea un utente e un token per Placet:
   ```bash
   docker exec -it ntfy ntfy user add --role=admin paolo
   docker exec -it ntfy ntfy token add paolo
   ```
3. Esponi ntfy sulla tailnet su una porta HTTPS separata da quella della PWA:
   ```bash
   tailscale serve --bg --https=8443 http://127.0.0.1:2586
   ```
4. Nell'app ntfy per iOS aggiungi il server `https://muletto.tail1234.ts.net:8443` con il tuo utente e iscriviti al topic.
5. Imposta `PLACET_NTFY_URL=https://muletto.tail1234.ts.net:8443`, `PLACET_NTFY_TOPIC`, `PLACET_NTFY_TOKEN` e `PLACET_BASE_URL`.

---

## 🤖 Configurazione degli Agenti

### 1. Avvia le sessioni tmux
Crea le sessioni tmux con i nomi convenzionati definiti in `AGENTS.md`:
```bash
tmux new -s agent-claude
tmux new -s agent-gemini
```

### 2. Calibra il riconoscimento del prompt

Il bridge scrive in una sessione solo se:
- l'agente è fermo su un prompt di input **vuoto**;
- sullo schermo non c'è un indicatore di lavoro in corso (es. "esc to interrupt");
- lo schermo non cambia tra due letture ravvicinate.

In tutti gli altri casi aspetta. Il caso più delicato è un menu o un dialogo aperto: l'Invio finale confermerebbe un'opzione.

I pattern del prompt vuoto stanno in `idle_patterns` di `AGENTS.md`, e quelli forniti sono da verificare sulla tua versione delle TUI. Avvia l'agente nella sua sessione tmux e controlla come la valuta il bridge:
```bash
.venv/bin/python -m bridge.calibrate claude --watch
```
Prova quattro situazioni: agente fermo, agente al lavoro, mentre scrivi e con un menu aperto. Solo la prima deve risultare `LIBERA`.

### 3. Configura il Server MCP di Placet
Aggiungi Placet alla configurazione MCP del tuo client (es. `claude_desktop_config.json`, `.claude.json` o configurazione Antigravity):

```json
{
  "mcpServers": {
    "placet": {
      "command": "/opt/placet/.venv/bin/python",
      "args": ["-m", "mcp_server.server"],
      "env": {
        "PYTHONPATH": "/opt/placet",
        "PLACET_API_URL": "http://127.0.0.1:8000",
        "PLACET_AGENT_ID": "claude",
        "PLACET_API_TOKEN": "<valore di PLACET_TOKEN_CLAUDE>"
      }
    }
  }
}
```

`PLACET_AGENT_ID` è facoltativo: l'identità deriva dal token, e se l'ID è impostato il backend verifica che corrisponda.

---

## 🛠️ Strumenti MCP Disponibili

1. `placet_ask_human(question, options, multiple, context, timeout_seconds)`
   - Richiede una decisione/scelta all'utente umano su iPhone.
   - Senza `options` è una domanda di conferma (approva/rifiuta) con nota facoltativa. Con `options` è una scelta singola, oppure multipla con `multiple=True`. L'opzione "Altro" a testo libero è sempre disponibile e non va passata tra le opzioni.
2. `placet_send_to_agent(target_agent, prompt, context, timeout_seconds)`
   - Invia un compito a un'altra sessione tmux registrata (es. `gemini`).
3. `placet_reply(request_id, response)`
   - Restituisce la risposta generata al completamento del task.
4. `placet_list_agents()`
   - Esplora gli agenti attivi, i loro ruoli e le regole di routing.
5. `placet_check_status(request_id)`
   - Interroga lo stato di una richiesta rimasta in sospeso oltre il timeout.

`timeout_seconds` è solo il tempo di attesa dello strumento (5–600 s). La richiesta resta valida fino alla sua scadenza (`expires_at`, default 24 ore), quindi puoi decidere anche dopo che lo strumento ha restituito `PENDING`.

Se l'attesa dello strumento finisce prima dell'esito, l'agente termina il turno. Quando l'esito arriva (tua decisione, risposta di un altro agente, rifiuto o scadenza), il bridge lo scrive nella sessione tmux dell'agente mittente con un messaggio che inizia con `[Placet]`. Il bridge aspetta 10 secondi, per lasciare il tempo a un'attesa ancora in corso di riceverlo direttamente, e lo fa solo quando il terminale è libero. Ogni esito viene consegnato una volta sola, e quelli più vecchi di 6 ore vengono ignorati.

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
