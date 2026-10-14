# Placet — Requisiti Applicativi

> **Versione:** 1.0 — 14/10/2026 · **Stato:** Consolidato post-intervista di design
>
> **Fonti:** Chat con ChatGPT del 09/10/2026, sessione con Claude Code del 10–14/10/2026, intervista /grill-me del 14/10/2026.

Ogni requisito ha uno stato:
- **[Deciso]** stabilito e confermato.
- **[Futuro]** pianificato per versioni successive all'MVP.

---

## 1. Scopo

Placet è un **punto unico di supervisione umana e bus di cooperazione per agenti AI**.
1. **Supervisione Human-in-the-Loop:** Gli agenti, di qualsiasi provider, chiedono decisioni e approvazioni, e tu rispondi comodamente dall'iPhone. Nessuna azione rilevante o messaggio pubblico viene eseguito senza il tuo placet.
2. **Cooperazione Cross-Sessione Controllata:** Sessioni aperte di provider diversi (Claude Code, Antigravity/Gemini, Codex, Ollama) possono inviarsi reciprocamente prompt, quesiti e compiti. Il prompt proposto passa prima dalla valutazione dell'utente (con possibilità di modifica rapida o auto-routing programmato da regole in `AGENTS.md`), e la risposta generata ritorna alla sessione chiamante.

**Problema che risolve:**
- Le app mobile dei provider non mantengono connessioni stabili con le sessioni CLI desktop.
- I provider lavorano in silos stagni: far collaborare una sessione Claude Code attiva con una sessione Gemini o Codex richiede oggi copia-incolla manuale.
- Placet unifica canale mobile e bus tra agenti sotto un unico protocollo standard (MCP + REST).

---

## 2. Attori

| Attore | Ruolo |
|---|---|
| **Utente** (Paolo) | Riceve notifiche push su iPhone, valuta, modifica e approva le richieste via PWA |
| **Agenti Mittenti** | Claude Code, Antigravity, Codex, agenti locali. Richiedono decisioni umane o collaborazione tra agenti |
| **Agenti Destinatari** | Sessioni CLI attive in tmux (`agent-claude`, `agent-gemini`, ecc.) che ricevono prompt e restituiscono risultati |
| **Backend Placet** | Server FastAPI + SQLite + notificatore `ntfy` su rete Tailscale. Fonte di verità e coordinatore |
| **Server MCP Placet** | Strumenti esposti a ciascun agente via stdio con autenticazione tramite variabili d'ambiente |
| **Bridge Locale** | Demone integrato sul PC host: controlla stato idle dei terminali e inietta i prompt via `tmux send-keys` |
| **Esecutore** | Servizio separato che esegue le azioni esterne (es. pubblicazione social) solo dopo approvazione formale |

---

## 3. Requisiti Funzionali

### 3.1 Richieste degli Agenti
| ID | Requisito | Stato |
|---|---|---|
| RF-01 | Gli agenti contattano Placet **tramite server MCP dedicato o API REST**, mai scrivendo direttamente nel database | [Deciso] |
| RF-02 | Le richieste umane supportano **scelta singola, scelta multipla, conferma o revisione prompt** | [Deciso] |
| RF-03 | Ogni domanda a scelta multipla include **sempre l'opzione "Altro"** a testo libero | [Deciso] |
| RF-04 | Placet espone un set di strumenti MCP semantici: `placet_ask_human`, `placet_send_to_agent`, `placet_reply`, `placet_list_agents`, `placet_check_status` | [Deciso] |
| RF-05 | Ogni richiesta include il contesto: mittente, destinatario, motivazione, prompt/testo proposto e opzioni | [Deciso] |
| RF-06 | L'agente può annullare una richiesta ancora pendente | [Deciso] |
| RF-07 | Le richieste hanno scadenza configurabile e priorità che orienta la notifica push | [Deciso] |

### 3.2 Comunicazione Cross-Sessione tra Agenti (Novità v1.0)
| ID | Requisito | Stato |
|---|---|---|
| RFC-01 | Un agente può indirizzare una richiesta a un'altra specifica sessione attiva registrata (es. `gemini`, `claude`) | [Deciso] |
| RFC-02 | Prima di recapitare il prompt alla sessione target, Placet sottopone il prompt alla revisione e approvazione dell'utente su iPhone | [Deciso] |
| RFC-03 | Supporto a regole di **Auto-routing** configurabili in `AGENTS.md` o dalla PWA per bypassare l'approvazione manuale su rotte fidate | [Deciso] |
| RFC-04 | Il Bridge locale inietta il prompt nella sessione tmux di destinazione solo dopo aver verificato lo stato **idle** del terminale (tramite `capture-pane`) | [Deciso] |
| RFC-05 | Il prompt iniettato contiene un **envelope standard** che istruisce l'agente ricevente a restituire l'output via tool MCP `placet_reply` | [Deciso] |

### 3.3 Risposte e Interazione Utente (iPhone)
| ID | Requisito | Stato |
|---|---|---|
| RF-10 | Interfaccia utente come **PWA installata sulla Home dell'iPhone**, servita su connessione sicura Tailscale | [Deciso] |
| RF-11 | Notifiche push immediate su iPhone tramite **ntfy self-hosted** in tailnet, con deep link alla PWA. Nessun pulsante Approva/Rifiuta nella notifica: si decide solo dalla PWA vedendo il testo completo (decisione del 14/10/2026) | [Deciso] |
| RF-12 | Possibilità nella PWA di **modificare il testo del prompt** direttamente nell'interfaccia prima di approvarlo | [Deciso] |
| RF-13 | **Storico e Audit Log** completo di tutte le interazioni, decisioni, modifiche e risposte scambiate | [Deciso] |

### 3.4 Ritorno della Decisione e Gestione Timeout
| ID | Requisito | Stato |
|---|---|---|
| RF-20 | Architettura **Long-polling con fallback asincrono**: lo strumento MCP attende fino a N minuti (default 180s). Se l'utente decide in tempo, la risposta ritorna sincrona nel tool | [Deciso] |
| RF-21 | Se scade il timeout del tool, questo restituisce `status: PENDING` e `request_id`; l'agente termina il turno pulito senza errori | [Deciso] |
| RF-22 | Al completamento della richiesta (approvazione o risposta dell'agente target), il Bridge locale re-inietta il risultato nel terminale dell'agente chiamante per risvegliarlo | [Deciso] |
| RF-23 | Una richiesta resta memorizzata e valida su SQLite anche in caso di riavvio del processo dell'agente o del demone bridge | [Deciso] |

---

## 4. Requisiti di Sicurezza

| ID | Requisito | Stato |
|---|---|---|
| RS-01 | Nessuna azione esterna o inoltro cross-provider avviene senza autorizzazione preventiva (manuale o governata da policy esplicite) | [Deciso] |
| RS-02 | Accesso al backend e alla PWA consentito **esclusivamente attraverso la rete privata Tailscale** (nessun IP pubblico aperto) | [Deciso] |
| RS-03 | **Autenticazione Agenti anti-spoofing:** ogni client MCP legge token dedicato e ID agente dalle variabili d'ambiente di configurazione del server MCP (`PLACET_AGENT_ID`, `PLACET_API_TOKEN`) | [Deciso] |
| RS-04 | **Chi propone non esegue:** gli agenti non detengono credenziali per azioni pubbliche o distruttive dirette | [Deciso] |

---

## 5. Decisioni Tecniche e Stack

| Componente | Tecnologia Scelta |
|---|---|
| **Backend & API** | Python 3.11+ · FastAPI · SQLite · SSE (Server-Sent Events) |
| **Server MCP** | Python SDK ufficiale (`mcp`) via stdio |
| **Bridge Locale** | Demone Python + comandi `tmux` (`send-keys`, `capture-pane`) |
| **Notifiche Push iPhone** | `ntfy` self-hosted in tailnet, solo azione "Apri" |
| **Frontend PWA** | React + Vite + Tailwind CSS |
| **Rete & Certificati** | Tailscale Serve (HTTPS e certificati automatici) |
| **Registry Agenti** | File `AGENTS.md` (YAML frontmatter + Markdown descrittivo) sincronizzato con DB |
| **Banco di Prova MVP** | Sessioni tmux `agent-claude` (Claude Code CLI) e `agent-gemini` (Gemini CLI) su Ubuntu (`paolo-pc-casa`) |
