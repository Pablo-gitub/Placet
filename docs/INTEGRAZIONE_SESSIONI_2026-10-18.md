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

`bridge/desktop_proxy.py`, `desktop_client.py` e il wrapper documentano un
esperimento di interposizione stdio, disabilitato nell'installazione attiva.
Il socket era stato creato, ma il riconoscimento dell'handshake non funzionava
con l'app reale. La correzione successiva è stata verificata nei test, non
validata con il desktop. CODEX_CLI_PATH rimane una dipendenza sperimentale.
Il percorso nativo non richiede né questo proxy né le sue variabili di ambiente.
Non confondere i test di protocollo con una validazione del desktop completo.

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
Codex→Codex con approvazione non è ancora stata provata end-to-end.

## Prossimo esperimento concordato

Il testo aggiunto alle menzioni è troppo invasivo per la lettura umana.
Si proverà a rimuovere le istruzioni operative aggiunte al messaggio,
conservando i riferimenti strutturati necessari per identificare le sessioni.
Questa modifica non è inclusa nei commit di consolidamento: il formato
attualmente installato resta invariato.
