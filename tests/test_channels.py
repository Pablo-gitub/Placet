"""Waking sessions inside the providers' apps (RF-24, RS-07)."""
import json
import subprocess
from unittest.mock import MagicMock

import httpx
import pytest

from bridge.bridge_daemon import BridgeDaemon
from bridge.channels import LS_SERVICE, AntigravityChannel, ChannelError, CodexChannel
from bridge.stores import SessionMail


def completed(code=0, out="", err=""):
    return subprocess.CompletedProcess([], code, stdout=out, stderr=err)


# --- Codex --------------------------------------------------------------------------


def test_codex_queues_the_message_with_a_binary_that_can(tmp_path, monkeypatch):
    old, new = tmp_path / "codex-old", tmp_path / "codex-app"
    for path in (old, new):
        path.write_text("")
        path.chmod(0o755)
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[1:] == ["queue", "--help"]:
            return completed(0 if cmd[0] == str(new) else 2)
        return completed(0, "Queued message")

    monkeypatch.setattr("bridge.channels.CODEX_CANDIDATES", (str(old), str(new)))
    monkeypatch.delenv("PLACET_CODEX_BIN", raising=False)
    channel = CodexChannel(run=run)
    channel.send("thread-1", "ciao")
    channel.send("thread-1", "di nuovo")
    sends = [c for c in calls if "--thread" in c]
    assert sends[0] == [str(new), "queue", "--thread", "thread-1", "--message", "ciao"]
    # The binary is probed once.
    assert len([c for c in calls if c[1:] == ["queue", "--help"]]) == 2


def test_codex_errors(tmp_path):
    binary = tmp_path / "codex"
    binary.write_text("")
    binary.chmod(0o755)
    channel = CodexChannel(binary=str(binary), run=lambda cmd, **k: completed(
        0 if "--help" in cmd else 1, err="no such thread"))
    with pytest.raises(ChannelError, match="no such thread"):
        channel.send("t", "x")
    with pytest.raises(ChannelError, match="PLACET_CODEX_BIN"):
        CodexChannel(binary=str(tmp_path / "missing")).send("t", "x")


# --- Antigravity -----------------------------------------------------------------------

CONFIG = {"plannerConfig": {"requestedModel": {"model": "MODEL_X"}}}


def ls_transport(seen, token="tok", steps=None, send_status=200):
    steps = steps if steps is not None else [
        {"type": "CORTEX_STEP_TYPE_USER_INPUT", "userInput": {"items": [{"text": "ciao"}], "userConfig": CONFIG}},
        {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE"},
        {"type": "CORTEX_STEP_TYPE_USER_INPUT", "userInput": {"items": [{"text": "poi"}], "lastUserConfig": {"x": 1}}},
    ]

    def handler(request: httpx.Request):
        seen.append(request)
        if request.url.port == 1111:
            raise httpx.ConnectError("refused")
        if request.headers.get("x-codeium-csrf-token") != token:
            return httpx.Response(403)
        method = request.url.path.rsplit("/", 1)[-1]
        assert request.url.path.startswith(f"/{LS_SERVICE}/")
        if method == "GetCascadeTrajectory":
            body = json.loads(request.content)
            if body["cascadeId"] != "conv-1":
                return httpx.Response(200, json={"trajectory": {"steps": []}})
            return httpx.Response(200, json={"trajectory": {"cascadeId": "conv-1", "steps": steps}})
        if method == "SendUserCascadeMessage":
            return httpx.Response(send_status, json={})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def channel(seen, **kwargs):
    return AntigravityChannel(
        client=httpx.Client(transport=ls_transport(seen, **kwargs)),
        discover=lambda: [(42, "tok", "2.9.1")],
        ports_of=lambda pid: [1111, 2222],
    )


def test_antigravity_sends_with_the_conversation_configuration():
    seen = []
    channel(seen).send("conv-1", "[Placet Request #3 dall'utente]: ciao")
    send = [r for r in seen if r.url.path.endswith("/SendUserCascadeMessage")][0]
    body = json.loads(send.content)
    assert body["cascadeId"] == "conv-1"
    assert body["items"] == [{"text": "[Placet Request #3 dall'utente]: ciao"}]
    assert body["deliveryStrategy"] == "MESSAGE_DELIVERY_STRATEGY_WHEN_IDLE"
    # The last full user configuration (without it the turn cannot start).
    assert body["cascadeConfig"] == CONFIG
    assert send.headers["Connect-Protocol-Version"] == "1"
    assert send.url.port == 2222


def test_antigravity_remembers_the_server_of_a_conversation():
    seen = []
    ch = channel(seen)
    ch.send("conv-1", "uno")
    seen.clear()
    ch.send("conv-1", "due")
    assert seen[0].url.port == 2222  # tried first


def test_antigravity_failures_name_the_version():
    with pytest.raises(ChannelError, match="Antigravity 2.9.1"):
        channel([]).send("other-conv", "x")
    with pytest.raises(ChannelError, match="answered 500"):
        channel([], send_status=500).send("conv-1", "x")
    with pytest.raises(ChannelError, match="model configuration"):
        channel([], steps=[{"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE"}]).send("conv-1", "x")
    nothing = AntigravityChannel(client=httpx.Client(transport=ls_transport([])), discover=lambda: [],
                                 ports_of=lambda pid: [])
    with pytest.raises(ChannelError, match="not running"):
        nothing.send("conv-1", "x")


# --- The bridge loop ----------------------------------------------------------------------


def test_daemon_confirms_or_gives_back():
    store = MagicMock()
    mails = {"a": SessionMail("injection", 1, "t1"), "b": SessionMail("outcome", 2, "t2"),
             "c": SessionMail("injection", 3, "t3")}
    store.take_session_mail.side_effect = lambda sid: mails[sid]
    good, bad = MagicMock(), MagicMock()
    bad.send.side_effect = ChannelError("down")
    daemon = BridgeDaemon(store=store, tmux=MagicMock(), channels={"codex": good, "antigravity": bad})
    sent = daemon.process_session_mail([
        {"id": "a", "provider": "codex"},
        {"id": "b", "provider": "antigravity"},
        {"id": "c", "provider": "antigravity"},
        {"id": "z", "provider": "unknown"},
    ])
    assert sent == 1
    store.confirm_injection.assert_called_once_with(1)
    store.release_delivery.assert_called_once_with(2)
    store.release_injection.assert_called_once_with(3)
    assert store.take_session_mail.call_count == 3  # nothing taken for an unknown provider
