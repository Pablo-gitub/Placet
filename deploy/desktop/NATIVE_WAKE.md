# Risveglio Codex tramite collegamento nativo

Il bridge include le sessioni Codex sospese fra quelle con posta in attesa.
Per ogni richiesta prende la posta tramite il backend, esegue `codex queue`
una sola volta, conferma l'accettazione e poi invoca:

```
/usr/bin/chatgpt codex://threads/<uuid>
```

La prova locale del 18 ottobre 2026 ha caricato una chat `notLoaded` e prodotto
la risposta attesa senza proxy. Dipende dalla sessione grafica dell'utente e
dall'app installata; l'accettazione del link non conferma l'avvio del turno.
Potrebbe portare la chat in primo piano. Nessun avvio headless viene eseguito.

Un errore/timeout del link dopo l'accodamento viene registrato, ma non annulla
l'accettazione e non causa un reinvio. La PWA indica che l'esecuzione non è
ancora osservata; in questo caso l'utente può riaprire la chat manualmente.
RF-29b continua a bloccare la richiesta successiva. La sospensione si elimina
solo con gli eventi della sessione: il link non inventa eventi di esecuzione.
Dopo accettazione nella coda Codex non è garantita la revoca del messaggio.

Non impostare PLACET_DESKTOP_SOCKET/PLACET_DESKTOP_THREADS nel bridge né
CODEX_CLI_PATH nel launcher. L'adattatore stdio nella stessa cartella è un
esperimento accantonato, non richiesto da questo flusso.

Dopo aggiornamento del codice in /opt/placet basta riavviare il bridge utente.
Backend, launcher e app Codex non richiedono modifiche ulteriori rispetto al
backend già installato che espone wake_sessions.

Verifica finale: scrivere una sola richiesta dalla PWA verso una chat sospesa,
verificare un solo turno nel rollout, risposta nella PWA e coda provider vuota.
Il test CLI già riuscito non sostituisce questa prova completa del bridge.
