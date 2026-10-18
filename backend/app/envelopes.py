"""Localized product wrappers around unchanged agent/user content.

Delivery channels: terminal input (tmux), app messages, or hook context.
Only the wrapper is translated; tools and request identifiers are stable.
"""
from backend.app.models import QuestionType, RequestModel, RequestStatus
from backend.app.i18n import text


def hook_preface(language=None):
    return text("envelope.hook", language)


# Compatibility constant for external callers; new callers use hook_preface().
HOOK_PREFACE = text("envelope.hook", "en")


def steer(req: RequestModel, language=None) -> str:
    if req.question_type != QuestionType.MESSAGE:
        return injection(req, ack_mode="none", channel="app", language=language) + "\n" + text("envelope.steer", language)
    message = text("envelope.user", language, id=req.id, prompt=req.prompt)
    message += "\n" + text("envelope.user_note", language) + "\n" + text("envelope.steer", language)
    return message + (f"\n\n{req.context}" if req.context else "")


def injection(req: RequestModel, ack_mode="mcp", channel="tmux", language=None) -> str:
    if req.question_type == QuestionType.MESSAGE:
        message = text("envelope.user", language, id=req.id, prompt=req.prompt)
        message += "\n" + text("envelope.user_note", language)
        if req.context:
            message += f"\n\n{req.context}"
    else:
        prompt = req.edited_prompt if req.edited_prompt is not None else req.prompt
        message = text("envelope.agent", language, id=req.id, sender=req.sender_id, prompt=prompt) + "\n"
        # RFC-06: whether the user reviewed this prompt; approval is not a blank cheque.
        if req.approval == "automatic":
            message += text("envelope.automatic", language) + "\n"
        elif req.approval == "manual":
            edited = req.edited_prompt is not None and req.edited_prompt != req.prompt
            message += text("envelope.reviewed_edited" if edited else "envelope.reviewed", language) + "\n"
        if ack_mode == "mcp" and channel == "tmux":
            message += text("envelope.ack", language, id=req.id)
        message += text("envelope.reply", language, id=req.id)
    return message + ("\n" + hook_preface(language) if channel == "hook" else "")


def outcome(req: RequestModel, channel="tmux", language=None) -> str:
    if req.recipient_id == "human":
        lines = [text("envelope.decision", language, id=req.id, status=req.status.value)]
        if req.selected_options:
            lines.append(text("envelope.options", language, options=", ".join(req.selected_options)))
        if req.custom_text:
            label = text("envelope.other" if req.options else "envelope.note", language)
            lines.append(f"{label}: {req.custom_text}")
        if req.edited_prompt is not None and req.edited_prompt != req.prompt:
            lines.append(text("envelope.edited", language, prompt=req.edited_prompt))
    else:
        lines = [text("envelope.outcome", language, id=req.id, recipient=req.recipient_id, status=req.status.value)]
        if req.edited_prompt is not None and req.edited_prompt != req.prompt:
            lines.append(text("envelope.prompt_changed", language, recipient=req.recipient_id, prompt=req.edited_prompt))
        note = {RequestStatus.DELIVERY_FAILED: "failed", RequestStatus.STALLED: "stalled", RequestStatus.EXPIRED: "expired"}.get(req.status)
        if note:
            lines.append(text(f"envelope.{note}", language))
        if req.return_decision == "forwarded":
            # The user sent the reply to another session: only a notice here.
            lines.append(text("envelope.reply_forwarded", language, responder=req.recipient_id,
                              request=req.return_forward_id or "?"))
        elif req.response is not None:
            origin = text("envelope.implicit", language) if req.response.implicit else ""
            content = req.return_text if req.return_text is not None else req.response.content
            lines.append(text("envelope.response", language, responder=req.response.responder_id, origin=origin, content=content))
    lines.append(text("envelope.resume", language, id=req.id))
    if channel == "hook":
        lines.append(hook_preface(language))
    return "\n".join(lines)
