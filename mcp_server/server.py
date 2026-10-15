import json
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
    prompt: str,
    context: Optional[str] = None,
    timeout_seconds: int = 180,
    priority: Optional[PriorityLevel] = None,
    target_session: Optional[str] = None,
) -> str:
    """
    Invia un prompt o un compito a un'altra sessione aperta e attiva di un agente registrato
    (es. 'claude', 'gemini', 'codex').
    Se richiesto dalle policy o dalle regole in AGENTS.md, la richiesta viene prima mostrata all'utente
    su iPhone per revisione/approvazione del prompt prima di essere iniettata nel terminale target.
    `target_session` (facoltativo): id di una sessione precisa dell'agente destinatario, da
    placet_list_agents; senza, Placet usa la prima sessione libera di quell'agente.
    Lo strumento attende la risposta dell'altro agente fino al timeout. Se restituisce uno stato
    non finale (PENDING, APPROVED, EXECUTING), termina il turno: Placet scriverà l'esito in questa
    sessione (messaggio che inizia con "[Placet]") quando sarà disponibile.
    """
    try:
        res = client.send_to_agent(
            target_agent=target_agent,
            prompt=prompt,
            context=context,
            timeout_seconds=timeout_seconds,
            priority=priority,
            target_session=target_session,
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
