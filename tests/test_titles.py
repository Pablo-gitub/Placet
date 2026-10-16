"""Titles of Codex and Antigravity sessions, read by the bridge (their hooks carry none)."""
import json
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from bridge.bridge_daemon import BridgeDaemon
from bridge.channels import AntigravityChannel
from bridge.stores import HttpStore
from bridge.titles import codex_titles
from tests.helpers import agent_client
from tests.test_session_messages import setup  # noqa: F401


def test_codex_titles_take_the_latest_rename(tmp_path):
    index = tmp_path / ".codex" / "session_index.jsonl"
    index.parent.mkdir()
    index.write_text("".join(json.dumps(e) + "\n" for e in [
        {"id": "t1", "thread_name": "Primo nome", "updated_at": "1"},
        {"id": "t2", "thread_name": "Altro", "updated_at": "2"},
        {"id": "t1", "thread_name": "Rispondi ok", "updated_at": "3"},
        {"id": "t3", "thread_name": "", "updated_at": "4"},
    ]) + "not json\n")
    assert codex_titles(["t1", "t3", "nope"], home=tmp_path) == {"t1": "Rispondi ok"}
    assert codex_titles(["t1"], home=tmp_path / "missing") == {}


def test_antigravity_titles_come_from_its_language_server():
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"trajectorySummaries": {
        "c1": {"summary": "Saluto Informale in Italiano"}, "c2": {"summary": "sviluppo"}, "c3": {}}}
    client = MagicMock()
    client.post.return_value = resp
    channel = AntigravityChannel(client=client, discover=lambda: [(1, "tok", "2.0")], ports_of=lambda pid: [4000])
    assert channel.titles(["c1", "c3"]) == {"c1": "Saluto Informale in Italiano"}
    assert client.post.call_args.args[0].endswith("/GetAllCascadeTrajectories")


@pytest.mark.asyncio
async def test_the_bridge_reports_titles_and_the_pwa_gets_them(setup, monkeypatch):  # noqa: F811
    app, db = setup
    for agent, sid, provider in (("codex", "cx1", "codex"), ("gemini", "ag1", "antigravity")):
        async with agent_client(app, f"tok-{agent}") as client:
            await client.post(f"/api/sessions/{sid}/events", json={"event": "start", "provider": provider})
    monkeypatch.setattr("bridge.bridge_daemon.codex_titles", lambda ids: {i: "Rispondi ok" for i in ids})
    agy = MagicMock()
    agy.titles.return_value = {"ag1": "Saluto"}
    daemon = BridgeDaemon(store=HttpStore(token="tok-bridge", client=TestClient(app)),
                          tmux=MagicMock(), channels={"antigravity": agy, "codex": MagicMock()})
    daemon.run_loop_step()
    assert db.get_session("cx1").title == "Rispondi ok"
    assert db.get_session("ag1").title == "Saluto"
    agy.titles.assert_called_once_with(["ag1"])
    daemon.run_loop_step()  # not again within the interval
    assert agy.titles.call_count == 1
    async with agent_client(app, "tok-codex") as codex:
        assert (await codex.post("/api/bridge/titles", json={"titles": {}})).status_code == 403
