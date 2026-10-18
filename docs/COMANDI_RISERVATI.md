# Comandi riservati: piano di implementazione

Stato: progetto concordato, funzionalità non ancora implementata.

## Obiettivo e perimetro

Eseguire dal telefono azioni che richiedono l'intervento umano, per esempio il deploy privilegiato. La schermata «Comandi» non è un terminale remoto e non introduce approvazioni sulle attività già autonome degli agenti: modifiche al codice, test, build e commit restano nel flusso attuale.

Il catalogo ammette soltanto azioni abilitate esplicitamente dall'amministratore tramite terminale. Non tutti i comandi sudo sono automaticamente ammessi. Possono essere incluse anche azioni senza sudo che si vuole riservare all'utente, purché esista un confine tecnico che ne impedisca l'esecuzione autonoma: nascondere un pulsante o scrivere una regola non impedisce a un agente con lo stesso utente Linux di eseguire un comando già accessibile.

Ogni esecuzione richiede una nuova autorizzazione tramite passkey con verifica dell'utente; sul telefono potrà essere Face ID. Nessuna password sudo viene raccolta o trasferita. Il catalogo autorizza il tipo di azione, non tutte le sue future esecuzioni.

## Flusso utente

1. La barra inferiore della PWA apre «Comandi», con le azioni abilitate, la loro descrizione e gli ultimi esiti.
2. L'utente seleziona un'azione e i soli parametri ammessi. Per il deploy: commit preciso, mai un riferimento mobile come `main` al momento dell'esecuzione.
3. La PWA apre la pagina di conferma del servizio amministrativo su un hostname separato. Il servizio ricalcola l'azione da mostrare; non si fida di una descrizione fornita dalla PWA.
4. L'utente vede azione, destinatario, parametri e versione. Per il deploy vede commit completo, autore, oggetto e cambiamenti rispetto alla versione installata, con attenzione a dipendenze e configurazione dei servizi.
5. Una passkey autorizza esattamente quell'operazione. Il servizio verifica l'asserzione e consuma atomicamente l'autorizzazione insieme alla creazione del job.
6. Un job indipendente esegue l'azione. La PWA mostra attesa, esecuzione, riuscita o fallimento; può riconnettersi dopo un deploy senza avviare nuovamente il job.

La chat potrà successivamente mostrare una proposta strutturata con «Rivedi ed esegui», che apre lo stesso flusso. Il testo libero, i blocchi shell e i messaggi degli agenti non diventano automaticamente comandi eseguibili. Nessun pulsante Play evita la pagina di conferma.

## Confini di fiducia

- Agenti e bridge possono proporre un'azione e leggere gli esiti consentiti. Non possono autorizzare, iscrivere credenziali, modificare il catalogo protetto o avviare un job privilegiato.
- Il backend Placet conserva i riferimenti alle proposte e presenta gli stati, ma non possiede una credenziale riutilizzabile per autorizzare l'esecuzione.
- Il servizio amministrativo verifica direttamente la passkey. Un campo `approved=true`, un token agente/bridge o il solo UID `placet` non sono sufficienti ad avviare l'azione.
- Catalogo, esecutore, pagina di conferma, chiavi pubbliche e configurazione dell'autenticazione vivono fuori da `/opt/placet`, sono protetti da root e non sono aggiornati dal normale deploy Placet.
- Il processo esposto alla rete e la porzione root devono essere ridotti al minimo e separati dove possibile. L'esecutore riceve soltanto un job già autorizzato con parametri validati, mai una shell arbitraria.
- L'identità Tailscale limita l'accesso ma non sostituisce il consenso crittografico. La conferma usa un RP ID separato da quello disponibile alla PWA; una porta diversa da sola non realizza questa separazione.
- L'origine della conferma non deve poter essere sostituita da Placet. La gestione di DNS, TLS e Tailscale Serve deve essere inclusa nella verifica dei privilegi dell'utente desktop prima di dichiarare il confine sicuro.

Una passkey prova l'autorizzazione dell'utente a quell'azione, non la correttezza del codice scelto. Gli agenti possono ancora modificare il repository e usare le credenziali disponibili al loro utente. La pagina di conferma deve rendere chiaro il commit autorizzato; la review del codice resta una decisione umana.

## Prima azione: deploy sicuro

L'attuale `deploy/install.sh` esegue come root uno script e dipendenze presi dal repository scrivibile dagli agenti, inclusa l'installazione pip; copia inoltre unit dal repository. Non va esposto direttamente come comando amministrativo remoto.

L'esecutore nuovo deve:

- risolvere e verificare un commit completo e l'identità del repository, quindi materializzare una copia isolata senza dipendere dal working tree modificabile;
- costruire frontend e dipendenze come utente dedicato, senza sudo, credenziali, accesso alla home di Paolo o socket dei servizi; considerare ostile anche il processo di build;
- non eseguire come root script, hook Git, programmi del venv o istruzioni provenienti dal commit;
- generare i servizi da template amministrativi protetti; mantenere una destinazione di installazione fissa;
- arrestare l'intero gruppo di processi di build prima di acquisire gli artefatti; rifiutare symlink, file speciali, percorsi esterni e metadati pericolosi; proteggere la snapshot acquisita da modifiche successive;
- pubblicare una release protetta e attivarla con passaggio atomico, evitando copie parziali sul codice in esecuzione;
- eseguire migrazioni applicative come utente di servizio, prevedendo backup e compatibilità dei dati: un rollback dei soli file non annulla una migrazione SQLite;
- conservare identificativo job, commit installato, risultato e log fuori dai processi riavviati.

Il bridge utente richiede un percorso distinto per il riavvio: non si estende genericamente il potere root a comandi arbitrari dell'utente. Ogni azione avrà un handler fisso e parametri limitati.

## Catalogo e autorizzazioni

Per ogni azione: ID stabile, descrizione umana, revisione del catalogo/handler, parametri tipizzati, privilegi necessari, timeout, politica di concorrenza e log ammessi. Nessun `shell=True`, comando libero, directory arbitraria, variabile d'ambiente libera o argomento aggiuntivo inserito dal richiedente.

Primo catalogo candidato: deploy di un commit preciso, riavvio backend, riavvio bridge. Abilitazione e modifica restano operazioni amministrative da terminale. Non inserire commit Git, test o build ordinarie nel catalogo.

La challenge è casuale, breve (indicativamente 60 secondi), monouso e legata lato server a: azione canonica, parametri, versione del catalogo/handler, artefatto o commit e riepilogo mostrato. Se cambiano gli elementi autorizzati o la versione installata rilevante, occorre una nuova conferma. Verificare firma, challenge, origine, RP ID e verifica dell'utente; non affidare l'antireplay al solo contatore dell'autenticatore.

Registrazione e revoca delle credenziali richiedono un comando amministrativo da terminale. Nessuna iscrizione iniziale autorizzabile da un agente o dalla sola sessione PWA. Prevedere una seconda credenziale e recupero tramite accesso amministrativo esistente.

## Stati e affidabilità

Proposta, attesa di conferma, autorizzazione scaduta/rifiutata, job accettato, in esecuzione, riuscito, fallito, esito da verificare. Autorizzare e creare il job è una singola operazione persistente; doppio tap, replay e retry HTTP restituiscono lo stesso job e non duplicano il comando.

Un deploy alla volta. I riavvii incompatibili attendono o vengono rifiutati con spiegazione. La perdita di connessione non equivale a fallimento: il telefono recupera lo stato del job. Un crash durante un'azione non idempotente non provoca un riavvio automatico alla cieca. L'eventuale annullamento è disponibile solo negli stati in cui l'handler lo garantisce.

I log non contengono password, token o materiale delle credenziali. I messaggi da build e job sono dati non attendibili, da mostrare come testo, non HTML o istruzioni eseguibili.

## Ordine di implementazione e criteri di accettazione

### 1. Esecutore e deploy isolato, verificabili da terminale

Realizzare catalogo, validatori, separazione della build, acquisizione degli artefatti e job persistenti. Produrre un bootstrap amministrativo separato, piccolo e revisionabile: nessuna installazione privilegiata automatica in questa fase.

Verifiche: alterare `install.sh`, requirements e unit del commit non deve eseguire codice root; nessun accesso dalla build alla home, alle credenziali o ai socket; processi figli e symlink non consentono modifiche dopo l'acquisizione; hash e parametri invalidi sono rifiutati; fallimenti non lasciano un'installazione a metà. Verificare separatamente backup/migrazioni/rollback.

### 2. Conferma indipendente con passkey

Realizzare origine amministrativa, registrazione da terminale, pagina protetta, challenge e consumo atomico con creazione del job. Verificare sul telefono reale compatibilità della passkey, apertura dalla PWA e ritorno all'app. Prima configurare e verificare hostname, TLS e relativi privilegi.

Verifiche: token degli agenti/bridge e UID backend non bastano; replay, challenge scaduta, parametri cambiati, verifica utente assente e origine errata falliscono; la PWA non può sostituire la pagina o autorizzare un'altra azione. Riavvio di Placet e crash dell'esecutore non duplicano il job.

### 3. Schermata Comandi nella PWA

Integrare catalogo disponibile, apertura della conferma esterna, monitoraggio e log. Mostrare chiaramente indisponibilità o configurazione incompleta; nessun pulsante eseguibile prima che il servizio protetto sia pronto. La PWA non riceve un segreto amministrativo permanente.

Verifiche: iPhone/PWA standalone, autenticazione annullata o scaduta, doppio tap, cambio rete, riconnessione durante deploy, esito recuperato dopo riavvio backend. Verificare che commit, test e strumenti ordinari degli agenti restino autonomi.

### 4. Proposte strutturate nella chat

Aggiungere un riferimento a un'azione abilitata e ai parametri candidati. Il pulsante apre la medesima conferma; nessuna nuova scorciatoia di autorizzazione. Testare riferimenti inesistenti, catalogo aggiornato e testo ingannevole rispetto all'azione reale.

## Stato operativo

Questo documento definisce il piano; non installa servizi, passkey, privilegi sudo o nuove route operative. Il bootstrap protetto richiederà un intervento amministrativo esplicito dopo la revisione dei file concreti. Termius/accesso amministrativo esistente resta il percorso di recupero.
