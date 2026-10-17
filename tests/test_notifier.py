from datetime import datetime, timezone

import httpx
import pytest

from backend.app.models import QuestionType, RequestModel
from backend.app.notifier import NtfyConfig, build_notification, send_ntfy_notification

PRIVATE = NtfyConfig(
    server_url="https://ntfy.tail1234.ts.net",
    topic="placet",
    base_url="https://placet.tail1234.ts.net",
    access_token="tk_secret",
)
PUBLIC = NtfyConfig(
    server_url="https://ntfy.sh",
    topic="placet-xyz",
    base_url="https://placet.tail1234.ts.net",
)


def _request(**overrides) -> RequestModel:
    data = dict(
        id=7,
        sender_id="claude",
        recipient_id="human",
        question_type=QuestionType.PROMPT_REVIEW,
        prompt="Pubblico il post sul lancio?",
        context="Piano editoriale Optees",
        created_at=datetime.now(timezone.utc),
    )
    data.update(overrides)
    return RequestModel(**data)


def test_private_server_includes_content_and_only_open_action():
    url, headers, body = build_notification(_request(), PRIVATE)
    assert url == "https://ntfy.tail1234.ts.net/placet"
    assert "Pubblico il post" in body and "Piano editoriale" in body
    assert headers["Click"] == "https://placet.tail1234.ts.net/#request-7"
    assert headers["Actions"].startswith("view, Apri, https://placet.tail1234.ts.net/#request-7")
    # No approve/reject from the notification: decisions happen in the PWA.
    assert "http," not in headers["Actions"]
    assert "decision" not in headers["Actions"]
    assert headers["Authorization"] == "Bearer tk_secret"


def test_public_server_never_carries_prompt_or_context():
    _, headers, body = build_notification(_request(), PUBLIC)
    assert "Pubblico il post" not in body
    assert "Piano editoriale" not in body
    assert "Pubblico" not in headers["Title"]
    assert "Authorization" not in headers


def test_long_prompt_is_truncated():
    _, _, body = build_notification(_request(prompt="x" * 1000, context=None), PRIVATE)
    assert body.endswith("...")
    assert len(body) < 400


def test_headers_are_ascii_safe():
    _, headers, _ = build_notification(_request(sender_id="agentè"), PRIVATE)
    for value in headers.values():
        value.encode("latin-1")


def test_disabled_without_full_configuration():
    assert not NtfyConfig(server_url="https://ntfy.sh", topic="t").enabled
    assert not NtfyConfig(topic="t", base_url="https://x").enabled


def test_from_env(monkeypatch):
    monkeypatch.setenv("PLACET_NTFY_URL", "https://ntfy.tail1234.ts.net/")
    monkeypatch.setenv("PLACET_NTFY_TOPIC", "placet")
    monkeypatch.setenv("PLACET_BASE_URL", "https://placet.tail1234.ts.net/")
    cfg = NtfyConfig.from_env()
    assert cfg.enabled and cfg.is_private
    assert cfg.server_url == "https://ntfy.tail1234.ts.net"
    assert cfg.base_url == "https://placet.tail1234.ts.net"


@pytest.mark.asyncio
async def test_send_posts_to_configured_server():
    seen = []

    def handler(request: httpx.Request):
        seen.append(request)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await send_ntfy_notification(_request(), PRIVATE, client=client)
    assert str(seen[0].url) == "https://ntfy.tail1234.ts.net/placet"


@pytest.mark.asyncio
async def test_send_reports_server_errors():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(403))) as client:
        assert not await send_ntfy_notification(_request(), PRIVATE, client=client)


@pytest.mark.asyncio
async def test_send_skipped_when_disabled():
    assert not await send_ntfy_notification(_request(), NtfyConfig())


def test_title_names_the_session_and_the_kind():
    _, headers, _ = build_notification(_request(), PRIVATE, origin="Claude · Refactor payments")
    assert headers["Title"] == "Claude - Refactor payments: domanda"  # ASCII header
    assert headers["Priority"] in ("default", "high", "urgent", "low")
    _, headers, _ = build_notification(_request(), PRIVATE)
    assert headers["Title"] == "claude: domanda"


def test_session_label():
    from backend.app.notifier import session_label
    assert session_label("codex", "Review PR", "/p/web") == "Codex · Review PR"
    assert session_label("antigravity", None, "/home/u/Projects/shop/") == "Antigravity · shop"
    assert session_label(None, None, None) == ""


def test_turn_end_is_quiet_and_opens_the_session():
    from backend.app.notifier import build_turn_end
    _, headers, body = build_turn_end("s-1", "Codex · Review PR", "Fatto: 3 commenti.", PRIVATE)
    assert headers["Title"] == "Codex - Review PR: turno finito"
    assert headers["Priority"] == "low" and headers["Click"].endswith("/#/sessione/s-1")
    assert body == "Fatto: 3 commenti."
