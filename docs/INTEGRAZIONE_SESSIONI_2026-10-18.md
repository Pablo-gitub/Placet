# Integrazione sessioni: stato verificato al 18 ottobre 2026

## Codex

Il percorso attivo usa il binario nativo: accodamento una sola volta, conferma
nel backend, quindi apertura di `codex://threads/<uuid>` tramite
`/usr/bin/chatgpt` per le sessioni sospese. Nessun fallback headless.
Il successo del comando di apertura non certifica l'avvio del turno.
Un errore dopo l'accodamento non rimette la richiesta in attesa e non causa
reinvii automatici; RF-29b conserva una richiesta in corso per sessione.

Prove reali senza proxy:
- “Rispondi ok”, inizialmente notLoaded: messaggio unico, apertura tramite
  link, risposta WAKE-OK-18 e successiva consegna della vecchia richiesta #39.
- “Progettista”: richiesta PWA #50, accodamento e apertura registrati dal bridge
  alle 11:06:37, risveglio confermato dall'utente.

La coda provider è stata letta in sola lettura. Non è stata implementata una
revoca garantita degli elementi già accettati da Codex.

## Esperimento accantonato

L'interposizione stdio fra l'app desktop e il suo app-server (`desktop_proxy.py`,
`desktop_client.py`, wrapper via CODEX_CLI_PATH) è stata tolta da `main`: il
riconoscimento dell'handshake non ha mai funzionato con l'app reale, e un
intermediario nel canale che esegue gli agenti era un rischio sproporzionato.
Il codice resta nel branch `experiment/desktop-proxy`. Il percorso nativo non
ne ha bisogno.

## Claude

Timeout asyncRewake impostato a sette giorni, rimosso il limite interno di
24 ore; lock flock e controllo dell'identità del processo engine proprietario.
I vecchi listener terminati richiedono il riavvio da un hook Claude.
La richiesta #46 è stata completata dopo il riavvio/apertura di Claude:
questo non distingue il contributo delle modifiche da quello del riavvio.
Resta da verificare la ricezione dopo una lunga inattività reale.

## Menzioni e collaborazione

Le menzioni umane condividono riferimenti, non inviano richieste. La verifica
dei permessi di routing avviene sull'effettiva richiesta dell'agente.
Il registro consente tutti i provider anche verso sessioni dello stesso
provider. Le regole Claude→Claude, Codex→Codex e Gemini→Gemini richiedono
approvazione umana. Resta vietata l'autoconsegna alla stessa sessione.
Lo script di migrazione aggiorna anche il registro installato creando backup;
il deploy ordinario non sovrascrive /etc/placet/AGENTS.md.
La menzione PWA #51 è stata ricevuta correttamente. La collaborazione reale
Codex→Codex è stata verificata con la richiesta #54, approvata e completata
con risposta TEST-COLLAB-OK.

## Scelta adottata: citazioni senza istruzioni operative aggiunte

Il testo operativo aggiunto alle menzioni rendeva i messaggi poco leggibili.
È stato rimosso il paragrafo che spiegava come usare placet_send_to_agent:
restano il testo dell'utente e i riferimenti JSON, separati da una riga vuota.
Gli ID completi impediscono di confondere sessioni con titoli simili.
Il breve avviso comune ai messaggi utente e i metadati degli allegati restano.

L'agente interpreta dal messaggio se l'utente sta semplicemente citando una
sessione oppure chiedendo un incarico. Permessi, approvazioni e routing sono
comunque applicati da Placet all'invio: non dipendono dal testo rimosso.

Prova reale: #52, citazione della sessione “Rispondi ok”, ricevuta senza
creare una richiesta; #53, incarico esplicito, tradotto nella richiesta #54
verso lo stesso ID e completato con TEST-COLLAB-OK. Il risultato valida
questi due casi in Codex, non garantisce l'interpretazione di ogni messaggio
ambiguo né costituisce una verifica equivalente sugli altri provider.

## Aggiornamenti della PWA e cache

Il server invia `Cache-Control: no-store` per la pagina iniziale, l'accesso
diretto a index.html e i percorsi della SPA. Manifest, icone e asset senza
versione richiedono rivalidazione; gli asset Vite con hash nel nome conservano
la cache annuale immutable. Il cambio di build aggiorna i nomi dei file.
I file mancanti sotto /assets restano 404, senza restituire HTML al loro posto.

La regola vale quando il client riceve la risposta aggiornata: non può revocare
una vecchia pagina già conservata da iPadOS/iOS né ricarica una pagina aperta.
La verifica HTTP automatizzata non sostituisce la riapertura reale della PWA
sull'iPhone dopo l'installazione. Non è presente un service worker nel progetto.
