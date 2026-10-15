# Placet — Requisiti Applicativi

> **Versione:** 2.0 — 16/10/2026 · **Stato:** Consolidato dopo il laboratorio multi-provider
>
> **Fonti:** Chat con ChatGPT del 09/10/2026, sessioni con Claude Code del 10–16/10/2026, intervista /grill-me del 14/10/2026, discussione e laboratorio del 15–16/10/2026 (risultati in [LABORATORIO.md](LABORATORIO.md)).

Ogni requisito ha uno stato:
- **[Implementato]** presente nel codice e verificato.
- **[Deciso]** stabilito e confermato, da implementare.
- **[Superato]** sostituito da un requisito più recente (indicato).
- **[Futuro]** pianificato per versioni successive.

---

## 1. Scopo

Placet è un **punto unico di supervisione umana e bus di cooperazione per agenti AI**, e dalla v2 anche il **pannello da cui seguire e guidare le proprie sessioni** dal telefono.

1. **Supervisione Human-in-the-Loop:** gli agenti, di qualsiasi provider, chiedono decisioni e approvazioni, e tu rispondi dall'iPhone. Nessuna azione rilevante o messaggio pubblico viene eseguito senza il tuo placet.
2. **Cooperazione cross-sessione controllata:** sessioni aperte di provider diversi (Claude Code, Codex, Antigravity/Gemini; in seguito altri) si inviano prompt e compiti lungo **relazioni** che definisci tu. Ogni relazione dice se il prompt passa da te (manual) o va diretto (automatic). La risposta torna sempre alla sessione chiamante.
3. **Le sessioni sul telefono:** leggi la conversazione di una sessione, rispondi alle sue domande dentro la conversazione, le scrivi anche quando non ti ha chiesto nulla, e la risvegli. Così riprendi il lavoro al PC o lo guidi da fuori.

**Problema che risolve:**
- Le app mobile dei provider non vedono le sessioni aperte sul PC, né quelle degli altri provider.
- I provider lavorano in silos: far collaborare una sessione Claude con una Codex o Gemini richiede copia-incolla manuale.
- Placet unifica canale mobile, bus tra agenti e vista delle sessioni sotto un unico protocollo (MCP + REST).

**Priorità dei provider:** Claude Code (app desktop e CLI), Codex (app desktop e CLI), Antigravity 2.0 (app e CLI `agy`). Claude Cowork/Progetti e ChatGPT Work sono desiderabili ma non prioritari [Futuro].

---

## 2. Attori

| Attore | Ruolo |
|---|---|
| **Utente** (Paolo) | Riceve notifiche su iPhone, legge le sessioni, approva o modifica richieste, scrive alle sessioni, definisce progetti e relazioni |
| **Sessioni** | Conversazioni aperte di un provider (app desktop o CLI). Mandano richieste e ne ricevono. Ogni sessione appartiene a un agente (`claude`, `codex`, `gemini`) |
| **Nodo** | La "sessione Placet" con nome stabile (nome, provider, cartella) a cui si aggancia la sessione reale; sopravvive a `/clear` e riaperture |
| **Backend Placet** | FastAPI + SQLite + ntfy su Tailscale. Fonte di verità e coordinatore. Non legge mai i file delle conversazioni |
| **Server MCP Placet** | Strumenti esposti a ciascun agente via stdio, con identità dal token |
| **Bridge locale** | Demone dell'utente sul PC: consegna i messaggi con il canale di ogni provider, legge le conversazioni a richiesta, raccoglie consumi e contesto |
| **Esecutore** | Servizio separato che esegue azioni esterne (es. pubblicazione) solo dopo approvazione [Futuro] |

---

## 3. Requisiti Funzionali

### 3.1 Richieste degli agenti
| ID | Requisito | Stato |
|---|---|---|
| RF-01 | Gli agenti contattano Placet **tramite server MCP o API REST**, mai scrivendo nel database | [Implementato] |
| RF-02 | Le domande all'umano supportano **scelta singola, scelta multipla, conferma o revisione prompt** | [Implementato] |
| RF-03 | Ogni domanda a scelta include **sempre l'opzione "Altro"** a testo libero | [Implementato] |
| RF-04 | Strumenti MCP: `placet_ask_human`, `placet_send_to_agent`, `placet_reply`, `placet_ack`, `placet_list_agents`, `placet_check_status` | [Implementato] |
| RF-05 | Ogni richiesta mostra mittente (con origine: progetto e sessione), destinatario, prompt e opzioni | [Implementato] |
| RF-05b | Il campo `context` diventa **superfluo**: il contesto si legge dalla chat della sessione mittente (RF-30). Si toglie da `placet_send_to_agent`; in `placet_ask_human` diventa facoltativo | [Deciso] — sostituisce l'obbligo introdotto il 15/10 |
| RF-06 | L'agente può annullare una richiesta ancora pendente | [Implementato] |
| RF-07 | Scadenza configurabile e priorità che orienta la notifica | [Implementato] |
| RF-08 | Un prompt per un altro agente può arrivare da un **file Markdown** (`prompt_file`); la PWA lo mostra leggibile e permette di modificarlo come testo | [Implementato] |
| RF-09 | **Attesa minima lato server** per le domande all'umano: un agente non può scegliere attese di pochi secondi. Se l'attesa scade, lo strumento dice chiaramente che l'esito arriverà con un risveglio | [Deciso] |

### 3.2 Comunicazione tra sessioni: progetti, relazioni e tipi
| ID | Requisito | Stato |
|---|---|---|
| RFC-01 | Un agente indirizza un prompt a una **sessione** specifica (nodo), non solo a un agente | [Implementato] per sessione; [Deciso] per nodo |
| RFC-02 | Le sessioni sono rappresentate da **nodi con nome stabile** (nome, provider, cartella). La sessione reale si aggancia al nodo e si riaggancia dopo `/clear` o riapertura | [Deciso] |
| RFC-03 | **Tipi di relazione** (id, nome, descrizione) riutilizzabili. La descrizione la legge l'AI per capire quando e perché scrivere a quella sessione | [Deciso] |
| RFC-04 | **Relazioni globali** A→B con un tipo e un'approvazione `manual` o `automatic`. Una relazione è unica per (A, B, tipo); tra le stesse sessioni possono esistere più relazioni con tipi diversi | [Deciso] |
| RFC-05 | `manual`: il prompt arriva a te, lo approvi, modifichi o rifiuti. `automatic`: va diretto a B | [Deciso] |
| RFC-06 | **L'agente non conosce mai l'approvazione**: testi ed esiti sono identici nei due casi, così pensa sempre che il prompt passi dall'utente | [Deciso] |
| RFC-07 | Senza relazione definita, l'invio è sempre `manual`; dalla card si può creare la relazione per le volte successive | [Deciso] |
| RFC-08 | API dell'agente: `send(target, type_id, prompt)`. Uno strumento elenca le relazioni che partono dalla sessione (destinazione, tipo, descrizione), mai l'approvazione | [Deciso] |
| RFC-09 | **Progetti**: raggruppano nodi e relazioni; un nodo e una relazione possono stare in più progetti. Se in un progetto riusi per A→B un tipo già usato altrove, l'app avvisa che la relazione è condivisa e propone un tipo diverso; puoi confermare | [Deciso] |
| RFC-10 | Le regole per coppia di agenti in `AGENTS.md` sono **sostituite** dalle relazioni. `AGENTS.md` resta per agenti, token e descrizioni | [Deciso] |
| RFC-11 | L'agente B riceve **solo il prompt**, con un preambolo che rende l'origine verificabile (può controllare la richiesta con uno strumento Placet) | [Implementato] solo prompt; [Deciso] origine verificabile |
| RFC-12 | Una sessione non riceve mai una richiesta propria; una richiesta per una sessione non viene consegnata a un'altra | [Implementato] |

### 3.3 Consegna e risveglio
| ID | Requisito | Stato |
|---|---|---|
| RF-20 | Long-polling con fallback asincrono: lo strumento attende; se scade restituisce `PENDING` e `request_id` | [Implementato] |
| RF-21 | **Chi aspetta una risposta viene sempre svegliato** quando arriva, con il canale del suo provider (RF-24). Non si conta sul fatto che l'agente stia ancora aspettando | [Deciso] — generalizza RF-22 |
| RF-22 | Al completamento il risultato viene consegnato alla sessione chiamante | [Implementato] per Claude; [Deciso] per Codex e Antigravity |
| RF-23 | Le richieste sopravvivono a riavvii di agenti, bridge e backend (SQLite) | [Implementato] |
| RF-24 | **Canale per provider**: Claude con hook + waiter asincrono; Codex con `codex queue`; Antigravity con l'API del suo language server; CLI in tmux con `send-keys` come ripiego. Dettagli in ARCHITETTURA §3 | [Deciso] |
| RF-25 | La consegna è confermata (ack) e la risposta raccolta anche senza `placet_reply` (risposta implicita dall'ultimo messaggio del turno) | [Implementato] per Claude; [Deciso] per gli altri |
| RF-26 | Il processo che consegna non dipende da una sessione aperta: gira nel bridge come servizio, e un waiter morto viene rilevato e sostituito | [Deciso] |
| RF-27 | Placet si accorge se una sessione **non ha il server MCP collegato** e lo segnala (sessione e PWA) | [Deciso] |

### 3.4 Le sessioni sul telefono
| ID | Requisito | Stato |
|---|---|---|
| RF-30 | La PWA mostra la **conversazione** di una sessione: in fondo l'ultimo messaggio e l'eventuale domanda, i precedenti si caricano scorrendo verso l'alto | [Deciso] |
| RF-31 | Lettura **a richiesta tramite il bridge**: nessuna copia delle conversazioni sul backend | [Deciso] |
| RF-32 | **Privacy**: si mostrano i messaggi dell'utente e le risposte testuali dell'agente; le azioni diventano una riga ("ha eseguito `cmd`"); risultati dei tool e ragionamento interno non si mostrano. Le compattazioni appaiono come separatore | [Deciso] |
| RF-33 | Si risponde alla domanda **dentro la conversazione** | [Deciso] |
| RF-34 | Si può **scrivere a una sessione** anche senza domande in corso; il messaggio la risveglia come se lo avessi scritto al PC, e nella PWA appare come messaggio tuo | [Deciso] |
| RF-35 | Se la sessione sta lavorando: **In coda** (a fine turno) o **Subito** (inserito nel turno in corso dopo la prossima azione). Un'impostazione dà il default, modificabile per ogni messaggio | [Deciso] |
| RF-36 | Dal telefono si possono **lanciare skill** (verificato anche sul desktop). Digitando `/` compaiono le skill e i comandi del provider, con la skill evidenziata | [Deciso] (elenco con `/` utile, non indispensabile) |
| RF-37 | PC spento: avviso "PC non raggiungibile" | [Deciso] |

### 3.5 Home, elenco sessioni e notifiche
| ID | Requisito | Stato |
|---|---|---|
| RF-10 | Interfaccia come **PWA installata sulla Home dell'iPhone**, servita su Tailscale | [Implementato] |
| RF-40 | **Home**: in cima le domande in corso; sotto le **sessioni aggiunte**, ordinate per attività (chi ha appena finito il turno in cima) | [Deciso] |
| RF-41 | **Aggiungi / vedi tutte**: scegli il provider, vedi le sue sessioni aperte, spunti quelle da aggiungere; le altre non compaiono in home | [Deciso] |
| RF-42 | Anche le sessioni che **non hanno mai usato Placet** si registrano, grazie agli hook installati globalmente | [Deciso] |
| RF-43 | Due tipi di notifica: **domanda** (priorità alta, suono) e **turno finito** (silenziosa, solo per le sessioni aggiunte). Titolo con provider e nome della sessione; il tocco apre la sessione | [Deciso] |
| RF-11 | Notifiche ntfy self-hosted in tailnet, con solo l'azione "Apri": si decide dalla PWA vedendo il testo completo | [Implementato] nel codice; ntfy da installare |
| RF-12 | Nella PWA si può modificare il prompt prima di approvarlo; il mittente viene informato della modifica | [Implementato] |
| RF-13 | Storico e audit completi, con timeline per richiesta | [Implementato] |
| RF-44 | **Grafo per progetto**: nodi e frecce orientate con il tipo; l'approvazione manual/automatic è visibile solo a te. Sezione per creare progetti, nodi, relazioni e tipi | [Deciso] |

### 3.6 Creare sessioni
| ID | Requisito | Stato |
|---|---|---|
| RF-50 | Dalla PWA si crea una sessione scegliendo provider, cartella (esistente o nuova), modello ed effort | [Deciso] |
| RF-51 | Le sessioni create da Placet sono **CLI in tmux** con Remote Control attivo: comandi veri (`/compact`, `/effort`) e riprendibili da app desktop, telefono e web | [Deciso] (verificato per Claude) |
| RF-52 | **Sessioni temporanee**: lavorano sulla cartella della sessione da cui nascono, con provider a scelta (es. Codex che rivede il lavoro di Claude); consegnano il risultato, si chiudono da sole e si possono eliminare | [Deciso] |
| RF-53 | Creare una sessione su una **cartella nuova** chiede conferma | [Deciso] |

### 3.7 Consumi, contesto e controlli
| ID | Requisito | Stato |
|---|---|---|
| RF-60 | **Barra dei consumi in home**, una riga per provider: `Gemini 5h 42% · reset 1h20 — 7d 60% · reset 3d5h` | [Deciso] |
| RF-61 | Nella sessione: **contesto** usato (`72.7k / 1M · 7%`), modello ed effort | [Deciso] |
| RF-62 | **Compact ed effort** da remoto dove il provider lo permette (CLI in tmux; Codex e Antigravity da verificare). Sulle sessioni desktop di Claude: "disponibile solo al PC" | [Deciso] |
| RF-63 | Il cambio di modello non è previsto (fa rileggere il contesto e consuma) | [Deciso] |

---

## 4. Requisiti di Sicurezza

| ID | Requisito | Stato |
|---|---|---|
| RS-01 | Nessuna azione esterna o inoltro tra sessioni senza autorizzazione, manuale o data da una relazione `automatic` che hai creato tu | [Deciso] |
| RS-02 | Backend e PWA raggiungibili **solo sulla tailnet** (nessun IP pubblico) | [Implementato] |
| RS-03 | Identità degli agenti dal token (`PLACET_API_TOKEN`), mai dal corpo della richiesta | [Implementato] |
| RS-04 | **Chi propone non esegue:** gli agenti non hanno credenziali per azioni pubbliche o distruttive | [Deciso] |
| RS-05 | **Scrivere a una sessione o crearne una equivale a comandare il PC da remoto**: consentito solo all'identità Tailscale autorizzata | [Deciso] |
| RS-06 | Il bridge legge solo i file di conversazione dei provider (es. `~/.claude/projects`, `~/.codex/sessions`, `~/.gemini/antigravity/brain`) e solo per sessioni registrate; mai un percorso passato dal backend | [Deciso] |
| RS-07 | Le API **non documentate** (es. language server di Antigravity) stanno dietro un adattatore isolato, con controllo di versione e ripiego (tmux + CLI) | [Deciso] |
| RS-08 | Le relazioni `automatic` sono da usare per scambi a basso rischio; tutto resta visibile nello storico | [Deciso] |

---

## 5. Decisioni Tecniche e Stack

| Componente | Tecnologia |
|---|---|
| **Backend & API** | Python 3.11+ · FastAPI · SQLite (WAL) · SSE |
| **Server MCP** | Python SDK ufficiale (`mcp`) via stdio |
| **Bridge locale** | Demone Python, servizio utente systemd, con adattatori per provider (consegna, lettura chat, consumi, comandi) |
| **Hook** | Claude Code (`~/.claude/settings.json`), Codex (`.codex/hooks.json`), Antigravity (`.agents/hooks.json`) |
| **Notifiche** | `ntfy` self-hosted in tailnet, solo azione "Apri" |
| **Frontend PWA** | React + Vite, Markdown con `react-markdown` (HTML grezzo ignorato) |
| **Rete** | Tailscale Serve (HTTPS) sul socket Unix del backend |
| **Registro agenti** | `AGENTS.md` (agenti, token, descrizioni); progetti, nodi, relazioni e tipi nel DB, gestiti dalla PWA |
| **Banco di prova** | `~/Projects/placet-hook-lab` (vedi [LABORATORIO.md](LABORATORIO.md)) |

---

## 6. Roadmap

| Fase | Contenuto |
|---|---|
| 1 | **Laboratorio** — fatto il 15–16/10/2026, risultati in [LABORATORIO.md](LABORATORIO.md) |
| 2 | **Sessioni sul telefono**: lettura della chat (RF-30…33), scrittura e risveglio (RF-34, RF-35), home con domande e sessioni aggiunte (RF-40…43); adattatori Claude, Codex, Antigravity nel bridge (RF-24…27) |
| 3 | **Nodi, relazioni e tipi** (RFC-02…10), nuova API `send(target, type_id, prompt)`, rimozione di `context` (RF-05b) |
| 4 | **Contesto, effort, compact e consumi** (RF-60…63) |
| 5 | **Creazione di sessioni** e sessioni temporanee (RF-50…53) |
| 6 | **Grafo ed editor dei progetti** (RF-44), `/` con le skill (RF-36) |

Dopo la fase 2: test di accettazione su tutte le combinazioni tra provider (Claude, Codex, Antigravity in entrambe le direzioni).
