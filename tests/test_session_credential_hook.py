"""RS-09: the Claude Code hook issues a session credential on every start (opt-in), sends only its hash,
and the MCP tools present it as a header when the model passes it."""
import hashlib
import json

import httpx

from hooks import claude_code
from mcp_server.client_api import PlacetClient


def test_off_by_default_nothing_is_issued(monkeypatch):
    monkeypatch.delenv("PLACET_SESSION_CREDENTIALS", raising=False)
    payload = claude_code.build_event({"hook_event_name": "SessionStart", "source": "startup", "cwd": "/p"})
    assert claude_code.issue_credential(payload) is None
    assert "credential_sha256" not in payload


def test_on_start_a_credential_is_issued_and_only_its_hash_is_sent(monkeypatch):
    monkeypatch.setenv("PLACET_SESSION_CREDENTIALS", "1")
    payload = claude_code.build_event({"hook_event_name": "SessionStart", "source": "compact", "cwd": "/p"})
    cred = claude_code.issue_credential(payload)
    assert cred.startswith("plst_") and len(cred) == 27
    assert payload["credential_sha256"] == hashlib.sha256(cred.encode()).hexdigest()
    assert cred not in json.dumps(payload)
    context = claude_code.startup_context(payload, credential=cred)
    text = context["hookSpecificOutput"]["additionalContext"]
    assert cred in text and "session_token" in text and not text.startswith("[Placet Request #")


def test_other_events_never_get_a_credential(monkeypatch):
    monkeypatch.setenv("PLACET_SESSION_CREDENTIALS", "1")
    for name in ("UserPromptSubmit", "Stop", "SessionEnd"):
        payload = claude_code.build_event({"hook_event_name": name, "cwd": "/p"})
        assert claude_code.issue_credential(payload) is None


def test_the_mcp_client_sends_the_token_as_a_header_only_when_given(monkeypatch):
    seen = []

    def fake_post(self, url, json=None, headers=None, **kw):
        seen.append(headers or {})
        return httpx.Response(200, json={"status": "COMPLETED"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    client = PlacetClient(base_url="http://x", api_token="t", agent_id="claude")
    client.reply(1, "ok", session_token="plst_" + "a" * 22)
    client.reply(1, "ok")
    client.ack(1, session_token="  ")
    assert seen[0]["X-Placet-Session"] == "plst_" + "a" * 22
    assert "X-Placet-Session" not in seen[1] and "X-Placet-Session" not in seen[2]
