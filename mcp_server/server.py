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
    context: Optional[str] = None,
    options: Optional[List[str]] = None,
    multiple: bool = False,
    timeout_seconds: int = 180,
    priority: Optional[PriorityLevel] = None,
) -> str:
    """
    Ask the human supervisor for a decision, approval, or choice on their phone.
    `context` (recommended): in 1–3 sentences, what you are doing, the
    consequences of each answer, and why a decision is needed. The user can
    also read your chat, but a bare "Proceed?" is hard to evaluate.
    Without `options`, the user can approve/reject and add a free-text note.
    With `options`, the user selects one (`multiple=False`) or more options.
    The UI always includes an Other/free-text choice; do not add one yourself.
    Read `status`, `selected_options`, and `custom_text` from the result.
    `priority` controls notification urgency (default high); reserve urgent for
    important blocked work. `timeout_seconds` waits at least 60 seconds so the
    user has time to read. If the result is still PENDING, end the turn without
    repeating the request. Placet posts the eventual decision in this session
    with a message beginning with "[Placet]" so work can resume.
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
    target_agent: Optional[str] = None,
    prompt: Optional[str] = None,
    prompt_file: Optional[str] = None,
    target_node: Optional[str] = None,
    relation_type: Optional[str] = None,
    rule_acknowledged: bool = False,
    target_session: Optional[str] = None,
    timeout_seconds: int = 180,
    priority: Optional[PriorityLevel] = None,
    session_id: Optional[str] = None,
) -> str:
    """
    Send a prompt to another session through Placet.
    `prompt` is the Markdown text the recipient receives (or `prompt_file`, an
    absolute .md/.txt path). Put every instruction in the prompt: the recipient
    sees only the prompt.
    Address the recipient in one of these ways:
    - `target_node` (+ `relation_type` when several types connect you): a node
      name from placet_list_relations. Prefer this when you have relations.
    - `target_agent` (+ optional `target_session` from placet_list_agents): an
      ad hoc request, always reviewed by the human.
`session_id` is your own session ID: pass it (as for placet_list_relations)
when Placet cannot tell which session is sending; with `target_node`, a
missing sender or relation is an error and nothing is sent.
    The result says `delivery_policy`: "manual" means the human may edit or
    reject the prompt; "automatic" means it was delivered without review.
    If the result status is RULE_ACKNOWLEDGEMENT_REQUIRED, NOTHING was sent:
    read `rule` now. If it tells you to stop or to ask the human first (with
    placet_ask_human), do that. Only otherwise send the same prompt again with
    rule_acknowledged=true. Never set rule_acknowledged without reading it.
    The tool waits up to `timeout_seconds`. For PENDING, APPROVED, DELIVERING
    or EXECUTING, end the turn without resending. COMPLETED with no `response`
    means the reply awaits the human's approval: do not repeat the request or
    fetch the other session's files to bypass it. Placet posts the outcome in
    this session with a message beginning with "[Placet]". Never target this
    session.
    """
    try:
        prompt_source = None
        if prompt_file:
            prompt, prompt_source = read_prompt_file(prompt_file)
        if not prompt:
            raise ValueError("Specify prompt or prompt_file.")
        if not (target_node or target_agent):
            raise ValueError("Specify target_node or target_agent.")
        res = client.send_to_agent(
            target_agent=target_agent,
            prompt=prompt,
            timeout_seconds=timeout_seconds,
            priority=priority,
            target_session=target_session,
            prompt_source=prompt_source,
            target_node=target_node,
            relation_type=relation_type,
            rule_acknowledged=rule_acknowledged,
            sender_session_id=session_id,
        )
        return json.dumps(res, indent=2, ensure_ascii=False)
    except Exception as e:
        return _format_error(e)


@mcp.tool()
def placet_list_relations(session_id: Optional[str] = None) -> str:
    """
    List the relations this session can use (RFC-08): for each, the target
    node, the relation type and its description (when and why to contact that
    session), `approval` ("automatic": delivered without human review;
    "manual": the human reviews), and `reply_policy` for the way back. Also
    returns the user's general `rule` for automatic relations: follow it.
    Call it before sending to another session. Pass `session_id` if Placet
    cannot tell which session you are.
    """
    try:
        return json.dumps(client.list_relations(session_id), indent=2, ensure_ascii=False)
    except Exception as e:
        return _format_error(e)


@mcp.tool()
def placet_ack(request_id: int) -> str:
    """
    Acknowledge a Placet request delivered through a terminal (a message
    beginning with "[Placet Request #N"). Call before starting the task when
    the delivery message requests it. Without acknowledgment, terminal
    delivery may be considered unconfirmed and retried.
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
    Record the result of another agent's Placet request, identified by request_id.
    Call after completing the requested analysis or task. This completes the
    task but does not bypass human approval of forwarding the reply to the
    requesting session. Direct user messages do not require this tool.
    """
    try:
        res = client.reply(request_id=request_id, response=response)
        return json.dumps(res, indent=2, ensure_ascii=False)
    except Exception as e:
        return _format_error(e)


@mcp.tool()
def placet_share_file(path: str, title: Optional[str] = None) -> str:
    """
    Show a generated PDF or JPEG/PNG/WebP image in the user's Placet phone
    conversation. Use for deliverables the user should view or download,
    rather than intermediate working files.
    `path` is an absolute path in the user's home, outside hidden directories
    such as .ssh or .config and outside projects excluded with .placet-off.
    `title` optionally supplies a display name.
    The file is read from the PC on demand and remains available only while
    it exists. There is no need to repeat its path in the final answer.
    """
    from bridge.attachments import AttachmentError, check_content, check_shared
    try:
        real = check_shared(path)
        with open(real, "rb") as f:
            check_content(f.read(21 * 1024 * 1024))
    except AttachmentError as e:
        return f"File not shared: {e}"
    except OSError as e:
        return f"File not shared: {e}"
    return f"File shared with the user: {title or real.name}. It is visible in the phone conversation."


@mcp.tool()
def placet_list_agents() -> str:
    """
    List registered agents, their roles and routing/approval rules, and known
    sessions: project (cwd), title, busy/idle state and reachability.
    Use exact session IDs when the user selects a target session.
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
    Read the current state of an existing Placet request. Do not resend it.
    COMPLETED means the recipient task finished, not that reply forwarding
    was authorized. If response is withheld, wait for the human decision.
    """
    try:
        res = client.check_status(request_id=request_id)
        return json.dumps(res, indent=2, ensure_ascii=False)
    except Exception as e:
        return _format_error(e)


if __name__ == "__main__":
    import asyncio
    asyncio.run(mcp.run_stdio_async())
