import json
import logging
from typing import List, Optional
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
    options: Optional[List[str]] = None,
    context: Optional[str] = None,
    timeout_seconds: int = 180,
) -> str:
    """
    Richiede una decisione, un'approvazione o una scelta all'utente umano (Paolo su iPhone).
    La richiesta viene notificata istantaneamente su iPhone. Se l'utente risponde entro il timeout,
    lo strumento restituisce la decisione, le opzioni scelte o il testo inserito/modificato.
    Se scade il timeout, restituisce lo stato PENDING con request_id per il risveglio successivo.
    """
    try:
        res = client.ask_human(
            question=question,
            options=options or [],
            context=context,
            timeout_seconds=timeout_seconds,
        )
        return json.dumps(res, indent=2, ensure_ascii=False)
    except Exception as e:
        return _format_error(e)


@mcp.tool()
def placet_send_to_agent(
    target_agent: str,
    prompt: str,
    context: Optional[str] = None,
    require_human_approval: bool = True,
    timeout_seconds: int = 180,
) -> str:
    """
    Invia un prompt o un compito a un'altra sessione aperta e attiva di un agente registrato
    (es. 'claude', 'gemini', 'codex').
    Se richiesto dalle policy o dalle regole in AGENTS.md, la richiesta viene prima mostrata all'utente
    su iPhone per revisione/approvazione del prompt prima di essere iniettata nel terminale target.
    """
    try:
        res = client.send_to_agent(
            target_agent=target_agent,
            prompt=prompt,
            context=context,
            require_human_approval=require_human_approval,
            timeout_seconds=timeout_seconds,
        )
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
    Restituisce l'elenco degli agenti registrati in Placet, i loro ruoli/specializzazioni,
    le sessioni tmux corrispondenti e le regole di approvazione attive.
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
