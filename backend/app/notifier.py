"""
Push notifications through ntfy.

The notification only announces the request and links to the PWA: decisions
are taken there, seeing the full text (no approve/reject buttons). Prompt and
context are included only when the ntfy server is private (self-hosted);
on a public server they would leave the tailnet.
"""
import logging
import os
from dataclasses import dataclass
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse

import httpx

from backend.app.models import Priority, RequestModel
from backend.app.i18n import text

logger = logging.getLogger(__name__)

PUBLIC_NTFY_HOSTS = frozenset({"ntfy.sh"})

# ntfy priorities: min, low, default, high, urgent.
NTFY_PRIORITY = {
    Priority.LOW: "low",
    Priority.NORMAL: "default",
    Priority.HIGH: "high",
    Priority.URGENT: "urgent",
}

BODY_PREVIEW_CHARS = 300


@dataclass(frozen=True)
class NtfyConfig:
    server_url: str = ""
    topic: str = ""
    # PWA address as seen from the iPhone (e.g. https://host.tailnet.ts.net).
    base_url: str = ""
    access_token: str = ""
    language: Optional[str] = None

    @classmethod
    def from_env(cls) -> "NtfyConfig":
        cfg = cls(
            server_url=os.environ.get("PLACET_NTFY_URL", "").rstrip("/"),
            topic=os.environ.get("PLACET_NTFY_TOPIC", ""),
            base_url=os.environ.get("PLACET_BASE_URL", "").rstrip("/"),
            access_token=os.environ.get("PLACET_NTFY_TOKEN", ""),
        )
        if not cfg.enabled:
            logger.warning(
                "ntfy notifications disabled: set PLACET_NTFY_URL, PLACET_NTFY_TOPIC "
                "and PLACET_BASE_URL to enable them."
            )
        elif not cfg.is_private:
            logger.warning(
                f"ntfy server {cfg.server_url} is public: notifications will not "
                "include prompt or context."
            )
        return cfg

    @property
    def enabled(self) -> bool:
        return bool(self.server_url and self.topic and self.base_url)

    @property
    def is_private(self) -> bool:
        host = (urlparse(self.server_url).hostname or "").lower()
        return host not in PUBLIC_NTFY_HOSTS


def _ascii_header(value: str) -> str:
    # HTTP header values must be latin-1; keep them plain ASCII.
    value = value.replace("·", "-").replace("→", "->").replace("…", "...")
    return value.encode("ascii", "replace").decode("ascii")


PROVIDER_LABELS = {"claude-code": "Claude", "codex": "Codex", "antigravity": "Antigravity"}


def session_label(provider: Optional[str], title: Optional[str], cwd: Optional[str]) -> str:
    """'Claude · Refactor payment service' (or the project when untitled)."""
    name = title or ((cwd or "").rstrip("/").rsplit("/", 1)[-1]) or None
    label = PROVIDER_LABELS.get(provider or "", provider or "")
    return " · ".join(p for p in (label, name) if p)


def build_notification(
    request: RequestModel, cfg: NtfyConfig, origin: Optional[str] = None
) -> Tuple[str, Dict[str, str], str]:
    """
    Returns (url, headers, body) for the ntfy publish call: a question (or a
    prompt to approve), loud. `origin` names the session it comes from
    ("Claude · <title>"); the tap opens that conversation (RF-43).
    """
    open_url = f"{cfg.base_url}/#request-{request.id}"
    sender = origin or request.sender_id
    if request.recipient_id == "human":
        title = text("notify.question", cfg.language, sender=sender)
    else:
        title = text("notify.review", cfg.language, sender=sender, recipient=request.recipient_id)

    if cfg.is_private:
        body = request.prompt
        if len(body) > BODY_PREVIEW_CHARS:
            body = body[:BODY_PREVIEW_CHARS] + "..."
        if request.context:
            body = f"{request.context}\n\n{body}"
    else:
        body = text("notify.public", cfg.language, sender=request.sender_id)

    headers = {
        "Title": _ascii_header(title),
        "Priority": NTFY_PRIORITY[request.priority],
        "Tags": "robot",
        "Click": open_url,
        "Actions": f"view, {text('notify.open', cfg.language)}, {open_url}, clear=true",
    }
    if cfg.access_token:
        headers["Authorization"] = f"Bearer {cfg.access_token}"

    return f"{cfg.server_url}/{cfg.topic}", headers, body


def build_turn_end(
    session_id: str, label: str, last_message: Optional[str], cfg: NtfyConfig
) -> Tuple[str, Dict[str, str], str]:
    """A followed session finished its turn: quiet, opens the conversation (RF-43)."""
    open_url = f"{cfg.base_url}/#/sessione/{session_id}"
    body = text("notify.turn_body", cfg.language)
    if cfg.is_private and last_message:
        body = last_message if len(last_message) <= BODY_PREVIEW_CHARS else last_message[:BODY_PREVIEW_CHARS] + "..."
    headers = {
        "Title": _ascii_header(text("notify.turn_title", cfg.language, label=label)),
        "Priority": "low",
        "Tags": "white_check_mark",
        "Click": open_url,
        "Actions": f"view, {text('notify.open', cfg.language)}, {open_url}, clear=true",
    }
    if cfg.access_token:
        headers["Authorization"] = f"Bearer {cfg.access_token}"
    return f"{cfg.server_url}/{cfg.topic}", headers, body


async def send_ntfy_turn_end(session_id: str, label: str, last_message: Optional[str], cfg: NtfyConfig) -> bool:
    if not cfg.enabled:
        return False
    url, headers, body = build_turn_end(session_id, label, last_message, cfg)
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(url, content=body.encode("utf-8"), headers=headers)
        return resp.status_code == 200
    except Exception as e:
        logger.warning(f"Unable to send turn-end notification: {e}")
        return False


def build_alert(
    request: RequestModel, cfg: NtfyConfig, problem: str
) -> Tuple[str, Dict[str, str], str]:
    """A delivery problem the human should look at (failed or stalled)."""
    open_url = f"{cfg.base_url}/#request-{request.id}"
    headers = {
        "Title": _ascii_header(f"Placet #{request.id}: {problem}"),
        "Priority": "high",
        "Tags": "warning",
        "Click": open_url,
        "Actions": f"view, {text('notify.open', cfg.language)}, {open_url}, clear=true",
    }
    if cfg.access_token:
        headers["Authorization"] = f"Bearer {cfg.access_token}"
    body = f"{request.sender_id} -> {request.recipient_id}: {problem}."
    return f"{cfg.server_url}/{cfg.topic}", headers, body


async def send_ntfy_alert(request: RequestModel, cfg: NtfyConfig, problem: str) -> bool:
    if not cfg.enabled:
        return False
    url, headers, body = build_alert(request, cfg, problem)
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(url, content=body.encode("utf-8"), headers=headers)
        return resp.status_code == 200
    except Exception as e:
        logger.warning(f"Unable to send ntfy alert: {e}")
        return False


async def send_ntfy_notification(
    request: RequestModel,
    cfg: NtfyConfig,
    client: Optional[httpx.AsyncClient] = None,
    origin: Optional[str] = None,
) -> bool:
    if not cfg.enabled:
        return False

    url, headers, body = build_notification(request, cfg, origin)
    try:
        if client is not None:
            resp = await client.post(url, content=body.encode("utf-8"), headers=headers)
        else:
            async with httpx.AsyncClient(timeout=5.0) as own_client:
                resp = await own_client.post(url, content=body.encode("utf-8"), headers=headers)
        if resp.status_code != 200:
            logger.warning(f"ntfy rejected notification for request #{request.id}: HTTP {resp.status_code}")
            return False
        return True
    except Exception as e:
        logger.warning(f"Unable to send ntfy notification: {e}")
        return False
