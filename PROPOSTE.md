# Proposte in discussione

Note di progetto non ancora implementate. Ogni punto resta qui finché non
viene deciso; poi passa in REQUISITI.md / ARCHITETTURA.md.

## 1. La sessione sul telefono

### Idea
La notifica non è più "una domanda isolata" ma **la sessione stessa**,
riprodotta nella PWA come appare in Claude Code:

1. Arriva una notifica "Claude · ⟨nome sessione⟩" (contratto API di Placet,
   consegnata via ntfy).
2. Aprendola si vede la conversazione della sessione: in fondo l'ultimo
   messaggio e la domanda in corso; scorrendo verso l'alto si caricano i
   messaggi precedenti.
3. Si risponde alla domanda nella conversazione stessa.
4. Si può aprire una sessione anche **senza domande in corso**, leggerla e
   scriverle: il messaggio la risveglia (canale hook) come se lo avessi
   scritto al PC.

### Decisioni prese
- **Lettura a richiesta tramite il bridge**, nessuna copia delle
  conversazioni sul backend. Il bridge gira come `paolo` e può leggere i
  transcript; il backend (`placet`) no, ed è giusto così.
- **Paginazione dal fondo**: prima pagina = ultimi messaggi, le precedenti
  arrivano con lo scroll.
- **Privacy**: si mostrano i messaggi dell'utente e le risposte testuali
  dell'agente. Le chiamate ai tool diventano una riga ("ha letto X",
  "ha eseguito `cmd`"); i loro risultati e il ragionamento interno
  (`thinking`) non vengono mostrati.
- Lettura **e** scrittura.

### Dati disponibili (verificato)
- Claude Code (CLI e app desktop) salva ogni sessione in
  `~/.claude/projects/<cartella>/<session_id>.jsonl`, una riga JSON per voce.
- Voci utili: `user` (testo o `tool_result`), `assistant` (`text`,
  `tool_use`, `thinking`). Da scartare: `attachment`, `queue-operation`,
  `custom-title`, `last-prompt`, `file-history-snapshot`, `system`, …
- Ogni hook riceve `transcript_path`: lo registriamo in SessionStart, così il
  bridge non deve indovinare il file.

### Schema proposto

```
PWA ──GET /api/sessions/{id}/transcript?before=<cursore>──▶ backend
backend ──job "leggi transcript" (long-poll già esistente)──▶ bridge
bridge legge il .jsonl dal fondo, filtra, restituisce una pagina
backend ──risposta (attesa max ~5 s)──▶ PWA
```

- Il bridge oggi non ascolta connessioni: interroga il backend. Le letture
  diventano un nuovo tipo di lavoro nella stessa coda (`/api/bridge/work`),
  con risposta su `/api/bridge/transcript/{job}`. Nessuna porta nuova.
- **Cursore** = posizione nel file (byte) della voce più vecchia restituita;
  il bridge legge a ritroso a blocchi, senza caricare file da centinaia di kB.
- **Aggiornamento dal vivo**: lo Stop hook genera già `session_update` via
  SSE; la PWA chiede allora solo i messaggi dopo l'ultimo che ha.
- **Sicurezza**: il bridge legge solo percorsi sotto `~/.claude/projects`
  (o equivalenti dei provider) e solo per sessioni registrate; mai un
  percorso arbitrario passato dal backend.
- **Adattatore per provider**: Claude (`.jsonl`) ora; Codex
  (`~/.codex/sessions`, da verificare quando installato) e Antigravity
  (`~/.gemini/antigravity`, formato da esplorare) dopo.
- Le compattazioni restano nel file: si mostrano come separatore
  "conversazione compattata".

### Scrittura in una sessione
- Un messaggio libero dalla PWA è una richiesta `human → sessione`
  (`recipient_session_id`), consegnata come i prompt tra agenti: canale hook
  per l'app desktop, tmux per la CLI.
- Se la sessione sta lavorando, il messaggio attende la fine del turno (Stop).
- Nell'app desktop il risveglio appare come "Stop hook feedback", non come
  un tuo messaggio: lo preambolo dirà "Messaggio di Paolo dalla PWA" e nella
  vista sul telefono lo mostriamo come messaggio tuo.
- Scrivere a una sessione equivale a darle un comando da remoto: l'accesso
  resta legato all'identità Tailscale.

### Decisioni (seconda tornata)
- **Home**: in cima il riquadro delle domande in corso; sotto l'elenco delle
  sessioni **aggiunte** dall'utente, ordinate per attività (chi ha appena
  finito il turno sta in cima).
- **Aggiungi / vedi tutte**: sotto l'elenco, un pulsante apre una finestra:
  scegli il provider, vedi le sue sessioni aperte, quelle già aggiunte hanno
  la spunta, spunti quelle da aggiungere. Le altre sessioni non compaiono
  nella home.
- **Anche sessioni che non hanno mai usato Placet**: hook installati
  globalmente (`~/.claude/settings.json`), così ogni nuova sessione si
  registra da sola. Le sessioni aperte prima dell'installazione restano in
  sola lettura finché non vengono riaperte.
- **Notifiche**: due tipi distinti.
  - Domanda: priorità alta, con suono.
  - Turno finito: silenziosa, solo per le sessioni aggiunte.
- **Messaggio dal telefono a una sessione che sta lavorando**:
  - *In coda*: arriva a fine turno (Stop).
  - *Subito*: entra nel turno in corso dopo la prossima azione dell'agente
    (output `additionalContext` dell'hook PostToolUse); se nel turno non ci
    sono più tool, arriva comunque a fine turno.
  - Un'impostazione generale fa da default; accanto a Invia si può cambiare
    per il singolo messaggio. A sessione ferma il messaggio parte subito.
- **PC spento**: avviso "PC non raggiungibile". Nella pratica la PWA non
  caricherà nemmeno, perché Tailscale non raggiunge il muletto.

## 2. Creare sessioni, consumi e controlli

### Creare una sessione
- Dalla PWA: provider, cartella (esistente, oppure nuova creata sul muletto),
  modello, effort.
- Placet non può creare sessioni nell'app desktop: le crea come **CLI in tmux**
  (gestite dal bridge), con gli hook già attivi.
- Al PC si possono **riprendere nell'app desktop**: il transcript è lo stesso
  formato e la stessa cartella `~/.claude/projects` (da verificare in lab).

### Sessioni temporanee
- Lavorano **direttamente sulla cartella** della sessione da cui nascono
  (possono leggere e modificare), con **provider a scelta** (es. Codex che
  rivede il lavoro di Claude).
- A lavoro finito consegnano il risultato e **si chiudono da sole**; la
  sessione (tmux e registrazione) si può eliminare.
- Riusano il canale tmux già funzionante.

### Consumi dei provider
- **Barra in home**, una riga per provider:
  `Gemini  5h 42% · reset 1h20 — 7d 60% · reset 3d5h`.
- Fonti da verificare per provider: Claude (dati `rate_limits` passati alla
  statusline), Codex (eventi `rate_limits` nei file di sessione), Gemini /
  Antigravity (da esplorare).

### Nella sessione a cui scrivi
- **Contesto**: `72.7k / 1M · 7%`, calcolato dai token dell'ultimo messaggio
  nel transcript (lo legge il bridge), così decidi se lanciare un compact.
- **Effort** modificabile; **compact** lanciabile.
- Cambio modello: non indispensabile (fa rileggere il contesto e consuma).
- Nelle sessioni CLI in tmux i comandi (`/compact`, `/effort`) si digitano
  davvero. Nell'app desktop il testo arriva come messaggio: **da provare** se
  l'agente lo interpreta e invoca la skill/il comando.

### Skill con `/`
- Digitando `/` nel campo messaggio: elenco delle skill e dei comandi di quel
  provider (letti dal bridge: skill utente, di progetto, plugin), con la skill
  evidenziata in un colore diverso nel testo. Utile, non indispensabile.

### Esperimenti da fare in lab prima di implementare
1. PostToolUse `additionalContext` entra nel turno in corso nell'app desktop
   (per l'invio "Subito").
2. Un comando/skill inviato via canale hook a una sessione desktop viene
   eseguito.
3. Una sessione CLI creata in tmux si riprende nell'app desktop.
4. Dove leggere i limiti 5h/7d per ciascun provider.

## 3. Progetti, relazioni tra sessioni e tipi

### Oggi
`placet_send_to_agent(target_agent, prompt | prompt_file, context,
target_session?, priority, timeout)`; l'approvazione dipende da regole per
**coppia di agenti** in `AGENTS.md`. `context` serve solo alla PWA.

### Proposta
- **Via il campo `context`**: con la lettura delle chat (punto 1) il contesto
  si legge direttamente dalla conversazione della sessione.
- Modello dati:

```
nodes            (id, nome, provider, cartella)    "sessione Placet" con nome stabile
                  └ la sessione reale aperta si collega al nodo; se viene
                    riaperta o fa /clear (nuovo session_id) si ricollega
projects         (id, nome)
project_nodes    (project_id, node_id)              un nodo in più progetti
relation_types   (id, nome, descrizione)            riusabile in più relazioni;
                                                    la descrizione la legge l'AI
relations        (id, da_node, a_node, type_id,     GLOBALE, unica per
                  approval: manual | automatic)     (da, a, tipo)
project_relations(project_id, relation_id)          un progetto mostra/usa relazioni
```

- **API agente**: `send(target, type_id, prompt)`. L'agente vede (tool di
  elenco) le relazioni che partono dalla sua sessione: destinazione, tipo,
  descrizione. **Mai** il campo `approval`.
- **manual**: arriva a te con la notifica, approvi / modifichi / rifiuti.
  **automatic**: passa direttamente a B. Per il mittente le due cose sono
  indistinguibili (stessi testi, stesso esito "approvato"): pensa sempre che
  passi dall'utente.
- **Senza relazione** definita: sempre manual; dalla card si può creare la
  relazione per le volte successive.
- Relazioni **globali**: A e B sono sempre le stesse sessioni (stessa
  cartella), quindi A→B di tipo T è una sola relazione, con una sola
  approvazione, anche se compare in più progetti.
- Quando in un progetto crei A→B con un tipo già usato per A→B altrove, l'app
  avvisa ("esiste già in Progetto_A: le modifiche valgono per entrambi") e
  propone un tipo diverso o uno nuovo; puoi comunque confermare e riusarla.
- Le regole per coppia di agenti in `AGENTS.md` vengono sostituite.

### PWA
- **Grafo per progetto**: nodi = sessioni, frecce orientate = relazioni,
  etichetta = tipo (e indicazione manual/automatic visibile solo a te).
- **Sezione Progetti**: crea progetto, aggiungi sessioni, disegna relazioni,
  gestisci i tipi.

## Risultati laboratorio

- **Esp. 1a — comando integrato via canale hook (desktop)**: `/compact`
  inviato come messaggio NON viene eseguito; l'agente lo riconosce come
  comando dell'interfaccia e risponde che non può lanciarlo. Compact /
  effort sul desktop: "solo al PC".
- **Problema trovato — waiter morto**: il waiter della sessione B si è fermato
  alle 07:49 (terminato da fuori durante un'attesa, nessuna traccia nei log)
  e nessuno lo ha riavviato fino al turno successivo: la sessione è rimasta
  irraggiungibile per ore. Va reso robusto (riavvio, o processo stabile per
  sessione).
- **Esp. 1b — skill via canale hook (desktop)**: FUNZIONA. Richiesta #11
  "esegui la skill /placet-banana": la sessione ha invocato la skill
  ("Ran skill") e risposto `BANANA-SKILL-OK`. Una skill creata a sessione
  aperta viene vista al turno successivo (non serve riaprire). Quindi dal
  telefono si possono lanciare skill anche sul desktop; i comandi integrati
  (`/compact`, `/effort`, `/model`) no.
- **Esp. 2 — "Subito" a metà turno (desktop)**: FUNZIONA. Un hook PostToolUse
  che restituisce `additionalContext` ha inserito il messaggio dopo la 4ª
  lettura (circa 7 s dopo la scrittura); la sessione si è fermata e ha
  scritto `BANANA-STEER`. L'invio "Subito" si può implementare così; se nel
  turno non ci sono più tool, il messaggio va consegnato a fine turno.
- **Esp. 1c — `/compact` da terminale su sessione desktop**: NON FUNZIONA.
  - Dalla skill: `claude -p --resume <id> "/compact"` viene bloccato dal
    controllo permessi (auto mode) della sessione.
  - Lanciato a mano: il file viene compattato davvero (68.0k → 3.3k,
    `compact_boundary` manuale), ma l'app desktop lo ignora: il turno dopo
    usa ancora 68k e si aggancia alla conversazione precedente (ramo
    parallelo nel file). Conclusione: compact ed effort sul desktop restano
    "solo al PC"; da remoto si possono fare solo sulle sessioni CLI in tmux.
- **Esp. 3 — sessione CLI (tmux) ripresa nell'app desktop**: NON COMPARE.
  La sessione `8078480a…` avviata in tmux su `placet-hook-lab` non è nella
  barra laterale dell'app (che mostra solo le sessioni create dall'app),
  nemmeno dopo F5. Pista alternativa: Remote Control (`/remote-control`
  nella CLI) per vederla in claude.ai/code / app.
- **Esp. 3b — CLI in tmux + Remote Control**: FUNZIONA.
  `/remote-control` nella sessione tmux la rende visibile e scrivibile da
  claude.ai/code e dall'app Claude. Richiesta #14 di Placet: consegnata dal
  waiter (canale hook), la sessione ha risposto `ARANCIA-REMOTE`, la risposta
  è tornata come risposta implicita (Stop hook) anche senza server MCP, e
  tutto era visibile nella vista Remote Control, aperta **dall'app desktop di
  Claude** (la sessione CLI è quindi recuperabile e scrivibile anche dal desktop,
  pur non comparendo tra le sessioni locali del progetto). Strada per le sessioni create
  da Placet: CLI in tmux + Remote Control → riprendibili da ogni dispositivo.
- **Osservazione — prompt "sospetti"**: la vecchia richiesta #6, consegnata
  via tmux a una sessione che non l'aveva mai vista, è stata rifiutata come
  possibile prompt injection. Il preambolo della consegna deve rendere
  l'origine verificabile (es. la sessione può controllare la richiesta con un
  tool Placet) e le richieste vecchie non vanno consegnate a sessioni diverse
  da quella prevista.
- **Osservazione — MCP scollegato**: le sessioni del laboratorio hanno perso
  il server MCP Placet. Causa reale (trovata dopo): avviando la CLI in tmux
  si è risposto "Continue without using this MCP server" al server di
  progetto, e la scelta è finita in `.claude/settings.local.json`
  (`disabledMcpjsonServers: ["placet"]`) per TUTTE le sessioni del progetto,
  desktop comprese; il disabilitato vince sull'abilitato. Inoltre l'app
  desktop sovrascrive `~/.claude.json` (modificarlo a mano mentre è aperta
  non serve). Una sessione ha anche cercato l'id breve tra le sessioni
  dell'app. Servono: nomi stabili (punto 3), un controllo che segnali
  "Placet non è collegato a questa sessione" (es. dall'hook SessionStart) e
  le sessioni create da Placet devono approvare il server in modo esplicito.
- **Esp. 4 — contesto, effort e limiti d'uso (Claude)**:
  - **CLI**: la statusline riceve tutto in JSON: `context_window`
    (es. 39.5k / 1M · 4%), `effort.level`, `model`, `cost`, `session_name` e
    `rate_limits.five_hour` / `seven_day` (`used_percentage`, `resets_at`).
    I limiti sono dell'account: basta una sessione CLI attiva (es. quella di
    servizio di Placet) per avere i consumi di Claude.
  - **Desktop**: l'app NON esegue la statusline. Contesto, modello ed effort
    si leggono dal transcript: ogni voce `assistant` ha `message.usage`
    (contesto = input + cache_read + cache_creation), `message.model`,
    `effort` e `perTurnEffort`. Dimensione della finestra dal modello.

## Ricognizione Codex e Antigravity (15/10/2026)

**Codex** — CLI `codex-cli 0.145.0` + app desktop (ChatGPT/Codex, in esecuzione).
- Sessioni: `~/.codex/sessions/AAAA/MM/GG/rollout-…-<id>.jsonl` (JSONL, come
  Claude): `response_item` (message, reasoning, tool call/output),
  `event_msg` (task_started/complete, token_count), `compacted`.
- Ogni `token_count` contiene contesto (`last_token_usage`,
  `model_context_window` = 475k) e **limiti**: `rate_limits.primary`
  (5h: 14%, reset) e `secondary` (7d: 2%, reset). Nessuna sessione di
  servizio necessaria: basta leggere l'ultimo rollout.
- Feature `hooks` stabile e attiva; config in `~/.codex/config.toml`
  (modello, effort).
- `codex app-server` (protocollo JSON-RPC con daemon e socket
  `~/.codex/ipc/ipc.sock`): possibile canale ufficiale per avviare turni su
  un thread esistente, anche dell'app desktop. Da esplorare.
- `codex resume`, `codex exec`, `codex mcp` per registrare Placet.

**Antigravity** — IDE `antigravity` 1.107 + CLI agente `~/.gemini/bin/agy`.
- Conversazioni: `~/.gemini/antigravity/conversations/<id>.db` (SQLite:
  `steps`, `trajectory_meta`, …) più `brain/<id>/…/messages/*.json`.
- `agy` ha: `--conversation <id>` (riprendi), `--effort`, `--model`,
  `-p` con `--output-format stream-json`, `--remote-control`, `agy mcp`
  (registrare Placet), plugin.
- Hook: da verificare. `mcp_config.json` vuoto.

### Esperimenti proposti
- C1. Registrare Placet come MCP in Codex e in `agy`; chiamata
  `placet_ask_human` da ciascuno.
- C2. Hook di Codex: quali eventi esistono (inizio turno, fine turno, tool)
  e se uno può risvegliare la sessione (equivalente di asyncRewake).
- C3. `codex app-server`: inviare un messaggio a un thread aperto nell'app
  desktop Codex.
- A1. Formato delle conversazioni `agy` (lettura chat) e dove stanno
  contesto e limiti.
- A2. Hook o meccanismo di risveglio in `agy`; in mancanza, tmux.
- **Esp. C1 — Placet in Codex**: FUNZIONA (MCP registrato come agente
  `codex`; domanda arrivata sul telefono). Codex ha scelto un'attesa breve e
  la risposta è arrivata dopo: l'ha vista solo chiedendo `placet_check_status`.
- **Requisito emerso**: chi aspetta una risposta deve essere SEMPRE
  svegliato quando arriva (Claude: waiter; Codex: da definire; tmux:
  send-keys). Attesa minima imposta lato server per le domande all'umano.
- **Esp. C2 — hook di Codex**: eventi come Claude (SessionStart,
  UserPromptSubmit, PreToolUse, PostToolUse, Stop, …) in
  `<progetto>/.codex/hooks.json`, ma **gli hook async non sono supportati**
  ("async hooks are not supported yet"): niente equivalente di
  asyncRewake. Gli hook di progetto vanno approvati (trust) in Codex.
  Strade per il risveglio: Stop sincrono con `decision: block` mentre la
  sessione aspetta una risposta; oppure sessioni gestite da Placet via
  `codex app-server` (`turn/start`, `turn/steer`, `thread/compact/start`,
  `thread/read`, `account/rateLimits/read`). L'app desktop Codex usa un
  app-server privato (stdio): non ci si aggancia ai suoi thread.
- **Esp. C3 — `codex queue` (risveglio Codex desktop)**: FUNZIONA (lato file).
  Il `codex` incluso nell'app desktop (`/usr/lib/chatgpt/resources/codex`,
  0.159.2; la CLI in PATH è 0.145 e non ha il comando) accetta
  `codex queue --thread <id> --message <testo>`. Il messaggio è entrato come
  messaggio utente nel thread aperto nell'app e ha avviato un turno
  (risposta `KIWI-CODEX` in 3 s). È il canale di risveglio per Codex:
  equivalente del waiter di Claude, ma senza processo in attesa.
  Confermato: messaggio e risposta compaiono da soli nell'app, in tempo reale.
- **Esp. A1 — chat di Antigravity**: leggibili.
  `~/.gemini/antigravity/brain/<conversation_id>/.system_generated/logs/transcript.jsonl`:
  JSON per passo (`step_index`, `source`, `type`, `status`, `created_at`,
  `content`, `thinking`, `tool_calls`). Tipi: `USER_INPUT`,
  `PLANNER_RESPONSE` (risposte del modello), `GENERIC` (esiti dei tool),
  `SYSTEM_MESSAGE`, `CHECKPOINT`, `ERROR_MESSAGE`. Il database SQLite
  `conversations/<id>.db` contiene gli stessi passi in protobuf.
  Contesto e limiti d'uso non sono nel transcript: da cercare.
- **Ricognizione A2 — risveglio Antigravity 2.0** (`/opt/antigravity-2`):
  - L'app avvia `agy --hub --hub-port=<porta> --csrf_token=<token>`: un
    server locale con API interne, tra cui `SendUserCascadeMessage`,
    `SendAllQueuedMessages`, `GetCascadeTrajectory`, `CancelCascadeInvocation`.
    Non documentate: funzionerebbero ma possono cambiare a ogni versione.
  - Hook supportati da `agy` (SessionStart, PreToolUse, PostToolUse, Stop,
    Notification) in `.agents/hooks.json` (progetto) o
    `~/.gemini/config/hooks.json` (utente). Async/rewake da verificare.
- **Esp. C4 — Claude → Codex attraverso Placet**: FUNZIONA. Richiesta #16
  da una chat Claude desktop del laboratorio alla sessione Codex desktop
  `01a1418c…`, approvata dal telefono. Un ponte di laboratorio
  (`placet-hook-lab/codex_lab_bridge.py`) registra il thread come sessione
  dell'agente `codex`, prende il messaggio dall'inbox, lo consegna con
  `codex queue`, aspetta `task_complete` nel rollout e manda lo stop con
  l'ultima risposta. Codex ha creato `codex-was-here.txt` e ha risposto da
  solo con `placet_reply`; l'esito è tornato alla sessione Claude. Tempo
  dalla consegna alla risposta: 24 s.
- **Problema trovato — ponte legato alla sessione**: il ponte avviato come
  processo in background di una sessione Claude Code muore quando l'app
  viene riavviata (successo due volte). Nell'implementazione il canale Codex
  va nel bridge di Placet (servizio utente systemd), non in una sessione.

### Riepilogo laboratorio

| | Claude desktop | Claude CLI tmux (+ Remote Control) | Codex desktop | Antigravity |
|---|---|---|---|---|
| Leggere la chat | ✅ transcript JSONL | ✅ transcript JSONL | ✅ rollout JSONL | ✅ transcript.jsonl |
| Messaggio da Placet (risveglio) | ✅ waiter + asyncRewake | ✅ waiter / send-keys | ✅ `codex queue` | da provare |
| Risposta a Placet | ✅ Stop hook / placet_reply | ✅ | ✅ placet_reply / rollout | da provare |
| Skill da remoto | ✅ | ✅ (presumibile) | da provare | da provare |
| "Subito" a metà turno | ✅ PostToolUse | da provare | `turn/steer` da provare | da provare |
| `/compact`, `/effort` da remoto | ❌ solo al PC | ✅ send-keys | da provare | da provare |
| Contesto | ✅ dal transcript | ✅ statusline | ✅ rollout | da trovare |
| Limiti 5h / 7d | via sessione CLI | ✅ statusline | ✅ rollout | da trovare |
| Visibile su altri dispositivi | ✅ | ✅ (anche dall'app desktop) | — | — |
- **Esp. A2 — hook di Antigravity (`agy`, documentazione inclusa nel
  binario)**: file `.agents/hooks.json` (progetto) con hook nominati; eventi
  `SessionStart`, `PreToolUse`, `PostToolUse`, `PreInvocation` (prima di
  ogni chiamata al modello), `PostInvocation`, `Stop`. Payload JSON in
  camelCase con `conversationId`, `workspacePaths`, `transcriptPath`,
  `modelName`. Hook **solo sincroni** ("no async execution"): nessun
  risveglio di una sessione ferma. Però:
  - `PreInvocation` → `injectSteps: [{"userMessage": "..."}]` o
    `ephemeralMessage`: canale "Subito" ancora più preciso di Claude.
  - `Stop` → `"decision": "continue"` + `reason`: per lo Stop che aspetta.
  - Risveglio di sessioni ferme: solo via API interna dell'app
    (`SendUserCascadeMessage` sul server `agy --hub`) o `agy` CLI in tmux.
- **Ricerca (fatta da Codex) su Antigravity 2.0**:
  - Nessun equivalente pubblico di `codex queue`. Candidato: RPC interna
    `exa.language_server_pb.LanguageServerService/SendUserCascadeMessage`
    (Connect, POST JSON, header `x-codeium-csrf-token`) sul language server
    dell'app (`/opt/antigravity-2/resources/bin/language_server`, porte
    locali, token nella riga di comando). Corpo candidato:
    `{"cascadeId", "items": [{"text"}], "deliveryStrategy":
    "MESSAGE_DELIVERY_STRATEGY_WHEN_IDLE"}` (esiste anche `NEXT_INVOCATION`,
    utile per "Subito"). Non documentata: da validare e da isolare dietro
    un adattatore, perché può cambiare a ogni versione.
  - `agy --conversation <id> -p …` riprende la conversazione in un altro
    processo: visibilità live nell'app non garantita.
  - Contesto e quote: `/context` e `/usage` nella CLI; la statusline della
    CLI ha un JSON con `context_window.used_percentage` e
    `quota.<bucket>.remaining_fraction` / `reset_time` (finestre 5h per
    Pro/Ultra, settimanali). Come per Claude: una sessione CLI di servizio.
  - Hook: si attivano in Settings → Customizations → Hooks (app) o `/hooks`
    (CLI); nessuna approvazione per hash.
- **Esp. A2b — hook di Antigravity 2.0 nell'app**: FUNZIONANO, senza
  approvazione (bastava aggiungere `placet-hook-lab` come progetto). Sequenza
  di un turno: `SessionStart` → (`PreInvocation` → `PostToolUse`* →
  `PostInvocation`)×N → `Stop` (`terminationReason: NO_TOOL_CALL`,
  `fullyIdle: true`). Ogni payload ha `conversationId`, `workspacePaths`,
  `modelName` (es. `gemini-3.8-flash-medium`), `transcriptPath`. Lo Stop non
  contiene l'ultima risposta: va letta dal transcript.
- **Esp. A3 — risveglio Antigravity 2.0 via API interna**: FUNZIONA (lato
  file). `SendUserCascadeMessage` sul language server dell'app
  (`http://127.0.0.1:<porta>`, header `x-codeium-csrf-token` letto dalla
  riga di comando del processo `language_server`) con `cascadeId`,
  `items: [{"text"}]`, `deliveryStrategy: WHEN_IDLE` **e `cascadeConfig`**:
  senza config il messaggio entra ma il turno fallisce ("neither PlanModel
  nor RequestedModel specified"). Con la `userConfig` dell'ultimo
  `USER_INPUT` della conversazione (letta con `GetCascadeTrajectory`) il
  turno parte: risposta `MELONE-GEMINI` in 1 s, hook PreInvocation/Stop
  eseguiti. Confermato: messaggi e risposta compaiono da soli nell'app. API non documentata: adattatore isolato, con controllo di
  versione e fallback (tmux + `agy`).

### Combinazioni tra provider: cosa è verificato

Ogni scambio A → B si compone di tre pezzi: A invia (server MCP Placet in
A), B viene svegliato (canale del provider B), la risposta di B torna a
Placet.

| Pezzo | Claude | Codex | Antigravity |
|---|---|---|---|
| Invia tramite Placet (MCP) | ✅ | ✅ (C1) | da provare (`agy mcp`) |
| Viene svegliato | ✅ waiter | ✅ `codex queue` | ✅ API interna |
| Risposta torna a Placet | ✅ Stop hook | ✅ placet_reply / rollout | da provare (Stop hook + transcript) |

Provati da capo a capo: Claude → Claude, Claude → Codex. Le altre
combinazioni (Codex → Claude, Antigravity ↔ Claude/Codex) diventano test di
accettazione dopo l'implementazione degli adattatori nel bridge.
