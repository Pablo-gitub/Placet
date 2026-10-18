# Adattatore desktop Codex (sperimentale, attivazione esplicita)

## Cosa fa

Il wrapper avvia **l'app-server che il desktop avrebbe avviato comunque** e
inoltra il suo stdio. Il bridge può leggere `thread/read` e caricare con
`thread/resume` sullo stesso server. Nessun secondo `initialize`, `turn/start`,
`exec resume`, database provider modificato, comando shell o RPC arbitrario.
Le notifiche, le approvazioni e le risposte ai tool continuano verso il desktop.

L'app installata 26.928.31416 legge `CODEX_CLI_PATH`: verificato nel pacchetto
locale. Questa impostazione e l'interposizione restano dipendenze sperimentali,
non un'interfaccia pubblica garantita da OpenAI. Il protocollo RPC è quello
documentato in https://learn.chatgpt.com/docs/app-server.

## Attivazione dopo installazione Placet

1. Prima chiudere normalmente Codex quando i turni sono finiti. Non ucciderlo
   mentre un agente lavora. Una seconda apertura dell'app già avviata NON applica
   le nuove variabili. In particolare non riavviarlo durante il turno che sta
   preparando questa integrazione.
2. Avviare l'app nella sessione grafica con:

   ```sh
   CODEX_CLI_PATH=/opt/placet/deploy/desktop/codex-wrapper \
   PLACET_DESKTOP_SOCKET="$HOME/.cache/placet/desktop/codex.sock" chatgpt
   ```

   Per una prova dal checkout impostare anche `PLACET_ROOT` e usare il percorso
   assoluto del wrapper nel checkout. Non sostituire il binario Codex originale.
3. Aggiungere la stessa variabile `PLACET_DESKTOP_SOCKET` al file di ambiente
   del bridge (`~/.config/placet/bridge.env`, percorso assoluto senza `$HOME`),
   e `PLACET_DESKTOP_THREADS=01a1467c-2982-78a0-8246-61283d1c3e80` per
   limitare il primo esperimento a “Rispondi ok”. La lista separata da virgole
   è obbligatoria: vuota, non abilita consegne tramite adattatore.
   Riavviare il bridge dopo avere installato anche il backend aggiornato.
   Nessun listener di rete viene aperto: directory 0700, socket 0600 e UID peer
   verificato dal server.
4. Prima usare una sola conversazione di laboratorio. Controllare in sola
   lettura vecchie consegne accettate/incerte: non rimandarle automaticamente.

Il bridge prima osserva/carica, poi prende una richiesta e usa il normale
`codex queue`. Se l'adattatore manca, il runtime è attivo o lo stato non è
riconosciuto, non prende il messaggio. Un errore dell'adattatore non abilita un
fallback headless. Una conferma runtime elimina la sospensione senza fingere
un evento hook di avvio turno o completamento. RF-29b continua a bloccare una
consegna già accettata di cui non si osserva l'esecuzione.

## Verifica reale ancora necessaria

- App avviata con wrapper: inizializzazione, caricamento e richiesta ordinaria
  dal desktop funzionano, incluse approvazioni e tool dinamici.
- Chat lasciata scaricare: una sola richiesta dalla PWA la carica e produce una
  sola risposta nel desktop e nella PWA, senza clic sul PC.
- Runtime occupato: il bridge non prende un'altra richiesta.
- Bridge o app disconnessi: messaggi restano in Placet; esiti incerti non vengono
  reinviati. Nessuna garanzia di annullamento dopo accettazione della coda Codex.
- Riavvio con app-server uscito: nessun socket vivo cancellato da un secondo
  wrapper; nessun messaggio RPC dell'adattatore arriva al dispatcher del desktop.

I test con engine fittizio verificano stdio + socket reali e conservazione delle
notifiche. NON provano che il desktop renderizzi correttamente thread caricati
dall'adattatore o consumi la coda: questa è la prova reale decisiva.

## Rollback

Chiudere normalmente l'app, rimuovere `PLACET_DESKTOP_SOCKET` dal bridge e
riavviarlo, poi avviare `chatgpt` senza `CODEX_CLI_PATH`. Nessun archivio app,
launcher di sistema o database provider è stato modificato. Vecchi messaggi
già accodati al provider restano tali: il rollback non li revoca.

## Claude

L'installatore imposta `timeout: 604800` sugli hook `asyncRewake`; il waiter non
ha più un limite interno di 24 ore. Il lock usa flock, resiste a PID riutilizzati
e viene liberato dal kernel anche in caso di terminazione forzata. Il waiter
segue il processo engine Claude identificato anche dal tempo di nascita,
anziché rimanere vivo perché esiste ancora Electron o una shell antenata.

Il timeout di sette giorni è una lease finita, non una promessa di disponibilità
permanente. Per coprire settimane senza turni servirà un canale persistente o
una riattivazione gestita dall'app. Non avviare manualmente il waiter: solo
Claude che l'ha lanciato come hook può raccoglierne l'uscita e risvegliarsi.
Prima applicare gli hook aggiornati con il normale installatore (backup), poi
ricaricare la sessione con l'app; il solo deploy non riattacca un hook morto al
processo già esistente. Verificare ricezione dopo almeno 15 minuti e dopo una
notte. La simulazione dei long-poll nei test non sostituisce queste prove.
