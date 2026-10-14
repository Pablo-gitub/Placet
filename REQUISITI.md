# Placet — Requisiti applicativi (bozza)

> **Versione:** 0.1 — 14/10/2026 · **Stato:** provvisorio, da rivedere insieme
>
> **Fonti:** chat con ChatGPT del 09/10/2026 (trascrizione in `~/Downloads/trascrizione_chat_antigravity_agenti_locali.md`) e sessione con Claude Code del 10–14/10/2026.

Ogni requisito ha uno stato:

- **[Deciso]** l'hai stabilito tu.
- **[Proposta]** emerso nelle discussioni, da confermare.
- **[Aperto]** non ancora deciso.

---

## 1. Scopo

Placet è un **punto unico di supervisione umana per agenti AI**. Gli agenti, di qualsiasi provider, ti chiedono una decisione, e tu rispondi dall'iPhone. Nessuna azione rilevante viene eseguita senza il tuo placet.

**Problema che risolve:** le app mobile dei provider (Claude, ChatGPT, Gemini) mantengono la connessione col desktop solo per un tempo limitato, e servirebbe usarne più d'una. Placet offre un unico canale, indipendente dal provider.

**Primo caso d'uso:** agenti "social media manager" per Optees ed Exlser (vedi §8). Placet però è generico e non legato ai social.

## 2. Attori

| Attore | Ruolo |
|---|---|
| **Utente** (Paolo) | Riceve le richieste e decide dall'iPhone |
| **Agenti** | Claude Cowork/Code, ChatGPT Work, Antigravity/Gemini, agenti locali (OpenJarvis + Ollama). Propongono azioni e attendono la decisione |
| **Backend Placet** | Riceve, conserva e notifica richieste e risposte. È la fonte di verità |
| **Bridge locale** | Gira sul PC dove girano gli agenti e li collega al backend (server MCP, adattatori, ripresa delle sessioni) |
| **Esecutore** | Servizio separato che esegue **solo** le azioni approvate |

## 3. Requisiti funzionali

### Richieste dagli agenti

| ID | Requisito | Stato |
|---|---|---|
| RF-01 | Gli agenti contattano Placet **tramite API**, mai scrivendo direttamente nel database | [Deciso] |
| RF-02 | Le domande possono essere a **scelta singola** o a **scelta multipla** | [Deciso] |
| RF-03 | Ogni domanda include **sempre l'opzione "Altro"**, con testo libero | [Deciso] |
| RF-04 | Placet è disponibile per gli agenti anche come **strumento MCP** (es. `request_placet`), oltre che come REST | [Proposta] |
| RF-05 | Ogni richiesta mostra il **contesto della decisione**: agente, piattaforma, cosa propone, obiettivo, contenuto esatto | [Proposta] |
| RF-06 | L'agente può **annullare** una richiesta ancora in attesa | [Proposta] |
| RF-07 | Le richieste possono avere una **scadenza** e una **priorità**, che decide il tipo di notifica | [Proposta] |

### Risposte dell'utente

| ID | Requisito | Stato |
|---|---|---|
| RF-10 | Interfaccia come **web app installata sulla Home dell'iPhone** (PWA) | [Deciso] |
| RF-11 | **Notifiche push** sull'iPhone all'arrivo di una richiesta ("se riesco": da verificare) | [Deciso] |
| RF-12 | Possibilità di **modificare il testo esatto** prima di approvare, oppure chiedere di rielaborarlo con indicazioni | [Proposta] |
| RF-13 | **Storico** consultabile di richieste e decisioni (audit log) | [Proposta] |

### Ritorno della decisione all'agente

| ID | Requisito | Stato |
|---|---|---|
| RF-20 | Il backend sul **PC o sul NAS fa da tramite**: la decisione arriva lì, e da lì parte la notifica all'agente | [Deciso] |
| RF-21 | **Modalità A, attesa:** lo strumento resta in attesa della risposta fino a un timeout configurabile | [Proposta] |
| RF-22 | **Modalità B, ripresa asincrona:** l'agente registra la richiesta e si ferma; riprende quando la decisione è disponibile, tramite polling, evento o avvio programmato | [Proposta] |
| RF-23 | Una richiesta resta valida anche se l'agente si interrompe o il PC si spegne | [Proposta] |

### Ciclo di vita di una richiesta [Proposta]

```text
PENDING ──► APPROVED ──► EXECUTING ──► COMPLETED
   │            │                   └─► FAILED
   ├──► REJECTED
   ├──► EXPIRED     (scadenza superata)
   └──► CANCELLED   (annullata dall'agente)
```

## 4. Requisiti di sicurezza

| ID | Requisito | Stato |
|---|---|---|
| RS-01 | Prima di un'azione pubblica (like, commento, post) l'agente **deve** ottenere l'approvazione | [Deciso] |
| RS-02 | Accesso **solo attraverso Tailscale**, nessuna esposizione su Internet | [Deciso] |
| RS-03 | Un'approvazione autorizza **una specifica azione** (es. *quel* commento con *quel* testo), non un permesso generico | [Proposta] |
| RS-04 | **Chi propone non esegue:** gli agenti non hanno i permessi di pubblicazione; l'esecutore separato esegue solo azioni approvate e identificate in modo univoco. Altrimenti un agente col browser già loggato potrebbe scavalcare Placet | [Proposta] |
| RS-05 | Ogni agente si autentica con un proprio token, così lo storico sa chi ha chiesto cosa | [Proposta] |

## 5. Requisiti non funzionali

| ID | Requisito | Stato |
|---|---|---|
| RNF-01 | **Indipendente dal provider:** Claude, GPT, Gemini e agenti locali usano lo stesso protocollo | [Deciso] |
| RNF-02 | **Si parte dal protocollo** di richiesta/risposta, non dall'interfaccia | [Proposta] |
| RNF-03 | **Leggero:** deve girare su un NAS o sul muletto senza risorse dedicate | [Proposta] |
| RNF-04 | Il componente che ospita il backend deve essere **sempre acceso** | [Proposta] |

## 6. Architettura proposta [Proposta]

```text
 iPhone — PWA Placet
 richieste, decisioni, notifiche
            ↕  Tailscale (HTTPS)  ·  push tramite i server Apple
 Backend Placet — NAS o muletto
 REST API · SQLite · coda richieste · Web Push
            ↕  rete locale / Tailscale
 PC — Agenti + Bridge locale
 Claude Cowork/Code · ChatGPT Work · Antigravity · OpenJarvis
 Bridge: server MCP · adattatori · ripresa sessioni
```

**Stack ipotizzato:** Python FastAPI · SQLite · PWA in React o Svelte · Web Push con VAPID · Tailscale Serve per l'HTTPS · Docker.

**Notifiche su iPhone:** Apple supporta Web Push per le web app installate sulla Home da iOS 16.4. La notifica passa dai server Apple, mentre il contenuto resta sul backend privato. È da verificare cosa succede quando Tailscale sull'iPhone è disconnesso: ricevere la notifica e riuscire ad aprire la richiesta sono due cose diverse.

### API — bozza delle operazioni

```text
create_request()      → crea una richiesta, restituisce l'id
get_request_status()  → stato attuale
get_request_result()  → decisione e testo eventualmente modificato
cancel_request()      → annulla una richiesta in attesa
```

### Modello dati — bozza

| Tabella | Contenuto |
|---|---|
| `requests` | Domanda, opzioni, tipo (singola/multipla), contesto, agente richiedente, priorità, scadenza, stato |
| `responses` | Decisione, opzioni scelte, testo "Altro", testo modificato, timestamp |
| `jobs` | Attività dell'agente collegata, stato della sessione, modalità di ripresa |

## 7. Criterio di fattibilità: il primo test da fare

Prima di costruire l'interfaccia va verificato che **una sessione di un agente possa chiedere, attendere e riprendere**:

1. Una sessione (Claude Code, Antigravity o Cowork) chiama lo strumento MCP.
2. Tu rispondi dall'esterno.
3. La stessa attività prosegue correttamente con la tua decisione.

Se funziona, il resto è una web app relativamente semplice. Se la ripresa delle sessioni è limitata, Placet resta comunque utilizzabile con agenti CLI, SDK e API.

## 8. Primo caso d'uso: social media manager

**Obiettivo dichiarato:** far crescere i follower su Instagram, LinkedIn e GitHub per Optees ed Exlser, con approvazione prima di ogni like, commento o post. [Deciso]

**Divisione dei compiti proposta, in base alla difficoltà:** [Proposta]

| Attività | Chi la svolge |
|---|---|
| Monitoraggio di fonti, GitHub e RSS | Script o API, senza LLM |
| Individuare e classificare opportunità | Modello locale piccolo (Ollama sul muletto) |
| Proporre commenti semplici | Modello locale o frontier |
| Preparare post importanti | Claude, GPT o Gemini |
| **Decidere cosa pubblicare** | **Tu, tramite Placet** |
| Pubblicare | Esecutore, con API ufficiali dove consentite, altrimenti a mano |

**Vincoli esterni da rispettare:**

- LinkedIn vieta like e commenti tramite automazioni non autorizzate.
- GitHub vieta attività artificiali per gonfiare follower e stelle.
- Instagram concede accesso limitato alle API.

Un'azione approvata da te non diventa per questo consentita dalla piattaforma.

**Collegamento esistente:** in `~/Projects/social` c'è già il piano editoriale di Exlser e Optees (canali IG, LinkedIn e Reddit; contenuti in IT e EN).

## 9. Fuori ambito per la prima versione [Proposta]

- Orchestrazione multi-agente (Placet si limita a ricevere, conservare e restituire decisioni).
- App nativa iOS.
- Like e commenti automatici in massa.
- Integrazione con Polygora (possibile in futuro, mantenendo il protocollo compatibile).

## 10. Ambiente disponibile (stato al 14/10/2026)

| Macchina | Ruolo possibile | Note |
|---|---|---|
| **Muletto** `paolo-pc-casa` | Agenti locali, eventualmente il backend | Ubuntu 26.04, i5-7500, 32 GB RAM, GTX 1050 Ti 4 GB (~2,4 GB liberi per i modelli), Docker, Ollama |
| **MacBook Pro M1 Max** 32 GB | Modelli locali più grandi (9B) | |
| **NAS** | Backend sempre acceso | Non è ancora nella tailnet |
| **iPhone 14 Pro Max** | Client PWA | Già nella tailnet |

## 11. Domande aperte

1. **Dove gira il backend:** NAS (quale modello? fa girare Docker?) o muletto?
2. **Notifiche:** Web Push nativo della PWA fin da subito, oppure ntfy come scorciatoia per il prototipo?
3. **Agenti del primo MVP:** quali partono per primi (Claude Code, Antigravity, OpenJarvis)?
4. **Nome dello strumento MCP:** `request_placet`, `ask_placet` o altro?
5. **Canali social:** GitHub (citato nella chat) o Reddit (presente nel piano editoriale)? O entrambi?
6. **Esecutore della pubblicazione:** API ufficiali dirette, Postiz (che però va installato in casa e richiede app sviluppatore approvate da Meta e LinkedIn), oppure pubblicazione manuale all'inizio?
7. **Autenticazione degli agenti** e gestione dei token.
