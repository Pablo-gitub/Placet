import json
from pathlib import Path
import logging
from typing import List, Literal, Optional

PriorityLevel = Literal["low", "normal", "high", "urgent"]
import httpx
from mcp.server.mcpserver import MCPServer
from mcp_server.client_api import PlacetClient

logger = logging.getLogger(__name__)

mcp = MCPServer(
    name="placet",
    description="Placet MCP Server: Human-in-the-Loop decision gatekeeper and Cross-Agent communication bus.",
)

client = PlacetClient()


MAX_PROMPT_FILE_BYTES = 200_000
PROMPT_FILE_SUFFIXES = {".md", ".markdown", ".txt"}


def read_prompt_file(path: str):
    """Reads a prompt prepared in a text/Markdown file: (content, file name)."""
    file = Path(path).expanduser()
    if not file.is_absolute():
        raise ValueError("prompt_file must be an absolute path.")
    if file.suffix.lower() not in PROMPT_FILE_SUFFIXES:
        raise ValueError(f"prompt_file must be one of {sorted(PROMPT_FILE_SUFFIXES)}.")
    if file.stat().st_size > MAX_PROMPT_FILE_BYTES:
        raise ValueError(f"prompt_file is larger than {MAX_PROMPT_FILE_BYTES} bytes.")
    return file.read_text(encoding="utf-8"), file.name


def _format_error(e: Exception) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        detail = str(e)
        try:
            body = e.response.json()
            if isinstance(body, dict) and "detail" in body:
                detail = body["detail"]
        except Exception:
            pass
        return json.dumps({
            "status": "ERROR",
            "code": e.response.status_code,
            "error": str(e),
            "detail": detail,
        }, ensure_ascii=False)

    return json.dumps({
        "status": "ERROR",
        "error": str(e),
        "detail": "Placet backend unreachable or encountered a network error.",
    }, ensure_ascii=False)


@mcp.tool()
def placet_ask_human(
    question: str,
    context: str,
    options: Optional[List[str]] = None,
    multiple: bool = False,
    timeout_seconds: int = 180,
    priority: Optional[PriorityLevel] = None,
) -> str:
    """
    Richiede una decisione, un'approvazione o una scelta all'utente umano (Paolo su iPhone).
    `context` è obbligatorio: la domanda arriva sul telefono senza nient'altro, mentre l'utente
    fa altro. Spiega in 1-3 frasi su cosa stai lavorando, cosa succede con ciascuna risposta e
    perché ti serve la sua decisione. Una domanda come "Procedo?" senza contesto non è decidibile.
    - Senza `options`: domanda di conferma (approva/rifiuta) con eventuale nota libera dell'utente.
    - Con `options`: l'utente sceglie un'opzione (`multiple=False`) o più opzioni (`multiple=True`).
      L'utente ha sempre anche l'opzione "Altro" con testo libero: non aggiungerla tu.
    Nella risposta: `status` (APPROVED = risposto/approvato, REJECTED = rifiutato),
    `selected_options` e `custom_text` (testo di "Altro" o nota).
    `priority` (low/normal/high/urgent, default high) decide quanto è insistente la notifica:
    usa urgent solo se un'attesa blocca qualcosa di importante.
    La richiesta viene notificata istantaneamente su iPhone. Se l'utente risponde entro il timeout,
    lo strumento restituisce la decisione, le opzioni scelte o il testo inserito/modificato.
    Se scade il timeout, restituisce lo stato PENDING con request_id: in quel caso termina il turno
    senza ripetere la domanda. Quando l'utente decide, Placet scrive l'esito in questa sessione
    (messaggio che inizia con "[Placet]") e potrai riprendere il lavoro.
    """
    try:
        res = client.ask_human(
            question=question,
            options=options or [],
            multiple=multiple,
            context=context,
            timeout_seconds=timeout_seconds,
            priority=priority,
        )
        return json.dumps(res, indent=2, ensure_ascii=False)
    except Exception as e:
        return _format_error(e)


@mcp.tool()
def placet_send_to_agent(
    target_agent: str,
    prompt: Optional[str] = None,
    prompt_file: Optional[str] = None,
    context: Optional[str] = None,
    timeout_seconds: int = 180,
    priority: Optional[PriorityLevel] = None,
    target_session: Optional[str] = None,
) -> str:
    """
    Invia un prompt a un'altra sessione di un agente registrato (es. 'claude', 'gemini', 'codex').
    - `prompt`: il testo (Markdown) che l'agente destinatario riceverà, e solo quello.
    - `prompt_file`: in alternativa, il percorso assoluto di un file .md/.txt con il prompt
      (ad esempio un prompt preparato in un file); il contenuto diventa il prompt.
    - `context`: spiega all'utente perché mandi questo prompt e cosa ti aspetti. Lo legge
      solo l'utente su iPhone, mentre decide se approvare o modificare: NON arriva al destinatario.
    L'utente può approvare, rifiutare o modificare il prompt prima dell'invio, secondo le regole
    in AGENTS.md.
    `target_session` (facoltativo): id di una sessione raggiungibile del destinatario, da
    placet_list_agents; senza, Placet usa la prima sessione libera di quell'agente.
    Lo strumento attende la risposta dell'altro agente fino al timeout. Se restituisce uno stato
    non finale (PENDING, APPROVED, DELIVERING, EXECUTING), termina il turno: Placet scriverà
    l'esito in questa sessione (messaggio che inizia con "[Placet]") quando sarà disponibile.
    """
    try:
        prompt_source = None
        if prompt_file:
            prompt, prompt_source = read_prompt_file(prompt_file)
        if not prompt:
            raise ValueError("Specify prompt or prompt_file.")
        res = client.send_to_agent(
            target_agent=target_agent,
            prompt=prompt,
            context=context,
            timeout_seconds=timeout_seconds,
            priority=priority,
            target_session=target_session,
            prompt_source=prompt_source,
        )
        return json.dumps(res, indent=2, ensure_ascii=False)
    except Exception as e:
        return _format_error(e)


@mcp.tool()
def placet_ack(request_id: int) -> str:
    """
    Conferma di aver ricevuto una richiesta Placet arrivata nel terminale
    (messaggio "[Placet Request #N da ...]"). Chiamalo subito, prima di iniziare il lavoro:
    senza conferma Placet considera il prompt non consegnato e lo reinvia.
    """
    try:
        res = client.ack(request_id=request_id)
        return json.dumps(res, indent=2, ensure_ascii=False)
    except Exception as e:
        return _format_error(e)


@mcp.tool()
def placet_reply(
    request_id: int,
    response: str,
) -> str:
    """
    Restituisce la risposta a una richiesta Placet ricevuta (indicata da request_id).
    Da chiamare una volta completata l'analisi o il task richiesto da un altro agente.
    """
    try:
        res = client.reply(request_id=request_id, response=response)
        return json.dumps(res, indent=2, ensure_ascii=False)
    except Exception as e:
        return _format_error(e)


@mcp.tool()
def placet_share_file(path: str, title: Optional[str] = None) -> str:
    """
    Mostra all'utente, nella conversazione sul telefono (PWA Placet), un file che hai
    prodotto per lui: un PDF o un'immagine (JPEG, PNG, WebP). Usalo quando l'utente deve
    vedere o scaricare il risultato, non per i file di lavoro intermedi.

    - path: percorso assoluto del file, nella home dell'utente e non in cartelle nascoste
      (mai .ssh, .config...); non in progetti esclusi con .placet-off.
    - title: nome da mostrare (facoltativo).

    Il file non viene copiato: l'utente lo legge dal PC finché esiste. Non serve
    citare il percorso nella risposta.
    """
    from bridge.attachments import AttachmentError, check_content, check_shared
    try:
        real = check_shared(path)
        with open(real, "rb") as f:
            check_content(f.read(21 * 1024 * 1024))
    except AttachmentError as e:
        return f"File non condiviso: {e}"
    except OSError as e:
        return f"File non condiviso: {e}"
    return f"File condiviso con l'utente: {title or real.name}. Lo vede nella conversazione sul telefono."


@mcp.tool()
def placet_list_agents() -> str:
    """
    Restituisce gli agenti registrati in Placet (ruoli, regole di approvazione) e le loro
    sessioni aperte: progetto (cwd), titolo, stato (busy/idle) e se Placet può raggiungerle.
    """
    try:
        res = client.list_agents()
        return json.dumps(res, indent=2, ensure_ascii=False)
    except Exception as e:
        return _format_error(e)


@mcp.tool()
def placet_check_status(
    request_id: int,
) -> str:
    """
    Interroga lo stato aggiornato di una richiesta precedente rimasta in sospeso (PENDING).
    """
    try:
        res = client.check_status(request_id=request_id)
        return json.dumps(res, indent=2, ensure_ascii=False)
    except Exception as e:
        return _format_error(e)


if __name__ == "__main__":
    import asyncio
    asyncio.run(mcp.run_stdio_async())
