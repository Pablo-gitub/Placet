"""
Texts Placet writes into an agent session: a prompt from another agent
(injection) or the outcome of a request the session made (outcome).

Three channels deliver them:
- tmux: typed into the terminal, so the user sees the text as a prompt;
- hook: handed to Claude Code by a background hook that wakes the session.
  The app does not show that text (it reaches the model as a system
  reminder framed as a "Stop hook blocking error"), so the agent is asked to
  quote it first: the user must see why the agent started working;
- app: queued by the bridge into the provider's app (Codex, Antigravity),
  where it appears as a user message.
"""
from backend.app.models import QuestionType, RequestModel, RequestStatus

OUTCOME_NOTES = {
    RequestStatus.DELIVERY_FAILED: "Il prompt non è stato confermato dall'agente destinatario dopo più tentativi.",
    RequestStatus.STALLED: "L'agente destinatario ha ricevuto il prompt ma non ha risposto entro il tempo massimo.",
    RequestStatus.EXPIRED: "La richiesta è scaduta.",
}

HOOK_PREFACE = (
    "Questo non è un errore: è un messaggio di Placet, il sistema di coordinamento "
    "tra te, l'utente e gli altri agenti. Inizia la tua risposta riportando in una riga "
    "chi ti scrive e cosa ti chiede (l'utente non vede questo messaggio), poi procedi."
)


USER_MESSAGE_NOTE = (
    "È un messaggio che l'utente ti scrive dal telefono tramite Placet: rispondigli "
    "direttamente, come a un suo messaggio (non serve placet_reply)."
)


def injection(req: RequestModel, ack_mode: str = "mcp", channel: str = "tmux") -> str:
    if req.question_type == QuestionType.MESSAGE:
        # The user's own message (RF-34): no reply tool, the answer is the turn.
        text = f"[Placet Request #{req.id} dall'utente]: {req.prompt}\n{USER_MESSAGE_NOTE}"
        if req.context:
            text += f"\n{req.context}"
        return f"{text}\n{HOOK_PREFACE}" if channel == "hook" else text
    # Only the prompt: the context explains the request to the human and is
    # not part of what the recipient receives.
    prompt_to_send = req.edited_prompt if req.edited_prompt is not None else req.prompt
    # Over the hook channel the delivery is confirmed by the turn it opens.
    ack_part = (
        f"Prima di iniziare chiama placet_ack(request_id={req.id}) per confermare la ricezione. "
        if ack_mode == "mcp" and channel == "tmux" else ""
    )
    text = (
        f"[Placet Request #{req.id} da {req.sender_id}]: {prompt_to_send}\n"
        f"{ack_part}Quando hai terminato, rispondi usando lo strumento MCP "
        f"placet_reply(request_id={req.id}, response=\"...\")."
    )
    return f"{text}\n{HOOK_PREFACE}" if channel == "hook" else text


def outcome(req: RequestModel, channel: str = "tmux") -> str:
    if req.recipient_id == "human":
        lines = [f"[Placet] Decisione dell'utente sulla richiesta #{req.id}: {req.status.value}."]
        if req.selected_options:
            lines.append(f"Opzioni scelte: {', '.join(req.selected_options)}")
        if req.custom_text:
            label = "Altro" if req.options else "Nota dell'utente"
            lines.append(f"{label}: {req.custom_text}")
        if req.edited_prompt is not None and req.edited_prompt != req.prompt:
            lines.append(f"Testo modificato dall'utente: {req.edited_prompt}")
    else:
        lines = [f"[Placet] Esito della richiesta #{req.id} a {req.recipient_id}: {req.status.value}."]
        if req.edited_prompt is not None and req.edited_prompt != req.prompt:
            lines.append(
                "Attenzione: l'utente ha modificato il tuo prompt prima di inviarlo. "
                f"{req.recipient_id} ha ricevuto ed eseguito questo testo: {req.edited_prompt}"
            )
        if req.status in OUTCOME_NOTES:
            lines.append(OUTCOME_NOTES[req.status])
        if req.response is not None:
            origin = (
                " (risposta implicita: ultimo messaggio dell'agente, non inviato con placet_reply)"
                if req.response.implicit else ""
            )
            lines.append(f"Risposta di {req.response.responder_id}{origin}: {req.response.content}")
    lines.append(
        "Riprendi il lavoro che avevi sospeso in attesa di questo esito "
        f"(dettagli con placet_check_status(request_id={req.id}))."
    )
    if channel == "hook":
        lines.append(HOOK_PREFACE)
    return "\n".join(lines)
