# Riattivazione delle conversazioni Claude Desktop

Quando esiste posta consegnabile per una sessione Claude Desktop inattiva e
senza heartbeat recente del waiter, `/api/bridge/work` la espone in
`claude_wake_sessions`. Sono escluse sessioni definitivamente chiuse, occupate, progetti
esclusi e sessioni CLI. SessionEnd delle sessioni desktop indica sospensione;
una migrazione una tantum recupera anche quelle prima classificate chiuse. Le nuove richieste devono essere indirizzate a un ID
preciso; una richiesta generica al provider non apre tutte le conversazioni.
Anche un esito da restituire alla sessione mittente può richiederne l'apertura.
RF-29b continua a bloccare una seconda richiesta quando la prima è in corso.

Il bridge legge i metadati locali sotto
`~/.config/Claude/claude-code-sessions/<account>/<org>/local_*.json`, cerca
`cliSessionId` e richiede una sola corrispondenza con lo stesso progetto,
non archiviata. Non modifica questi file. Poi invoca tramite argv, senza shell:

```
/usr/bin/claude-desktop claude://code/continue?session=local_<id-desktop>
```

Questo formato è stato verificato nel pacchetto installato e nella prova reale
su “Ciao”, ma non è un'API pubblica garantita. Se il mapping manca, è ambiguo
o non coincide, non viene scelta un'altra sessione e non si avvia una CLI
headless. Un errore resta nei log; la PWA mantiene il messaggio in attesa e
spiega che occorre aprire la conversazione nell'app se non torna raggiungibile.

## Consegna e limiti

Il bridge non prende la posta Claude, non la conferma e non dichiara avviato
un turno. Il waiter lanciato da Claude tramite asyncRewake resta l'unico
responsabile della consegna. Aprire il collegamento non garantisce che il
waiter parta: si attende un heartbeat e poi gli eventi reali del turno.
Il link può portare una finestra in primo piano.

Ogni tentativo, anche fallito, è distanziato di almeno cinque minuti per
sessione. La registrazione privata in `~/.cache/placet/claude-wake/` usa flock
e viene scritta prima dell'apertura, così il limite sopravvive al riavvio del
bridge. Non viene registrato il contenuto dei messaggi. Se il waiter risulta
ancora raggiungibile il backend non richiede un'apertura.

Un engine rimasto vivo ma privo di waiter potrebbe non rilanciare gli hook
con la sola apertura: occorre una verifica manuale della sessione, eventualmente
un turno locale o un riavvio controllato quando non sta lavorando. Non si tenta
di attaccare dall'esterno un waiter a un engine esistente.

## Prerequisiti e verifiche

- Codice aggiornato di backend e bridge, servizi riavviati.
- Hook Claude reinstallati con `python3 hooks/install.py --providers claude`:
  il deploy del codice non aggiorna da solo `~/.claude/settings.json`.
- Hook asyncRewake con timeout di sette giorni, configurazione riletta da Claude.
- Sessione grafica dello stesso utente e launcher `/usr/bin/claude-desktop`.

Prova manuale già riuscita il 18 ottobre 2026: app riavviata, “Ciao” non aperta,
nessun engine/waiter della sessione; apertura del link preciso, stesso ID CLI,
risposta al messaggio pendente #57 e nuovo waiter vivo dopo Stop. Non è stato
reinviato il messaggio. Questa prova precede l'automazione del bridge.

Dopo installazione verificare un'altra consegna dalla PWA con conversazione
non caricata e poi una dopo almeno 15 minuti di inattività. La simulazione del
cooldown nei test non prova la durata reale del listener. Per il rollback
ripristinare backend/bridge della versione precedente: il collegamento non
scrive cronologie e i messaggi non ritirati restano nella coda Placet.

## Aperture solo quando necessarie

Claude già in attesa con heartbeat recente: nessuna apertura. Claude occupato:
nessuna apertura. Codex già in attesa: solo queue, nessun collegamento.
Codex sospeso: queue e collegamento. Si usano gli eventi osservati da Placet;
per Codex non c'è una query affidabile al runtime privato dal bridge, quindi
uno stato obsoleto può ancora causare un'apertura superflua. Per Claude un
heartbeat recente può ritardare il recupero fino alla scadenza del TTL.
La migrazione distingue solo le sessioni riconosciute come claude-desktop:
le sessioni CLI chiuse non vengono rese inviabili. L'archiviazione è verificata
nel mapping dell'app prima dell'apertura; la sospensione non implica che una
conversazione archiviata o eliminata possa essere ripresa automaticamente.
