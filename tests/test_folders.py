"""Opt-in per project: which folders go through Placet, switched from the PWA."""
import asyncio
import json
import os
import tempfile

import pytest

from backend.app.config import AgentSpec, PlacetConfig
from backend.app.database import Database
from backend.app.main import create_app
from bridge.bridge_daemon import BridgeDaemon
from bridge.folders import FolderError, known_folders, set_folder, state_of
from bridge.stores import HttpStore
from tests.helpers import agent_client, human_client


def make_home(tmp_path):
    home = tmp_path / "home"
    for name in ("Projects/alpha", "Projects/beta/sub", "Projects/gamma", "Projects/lab"):
        (home / name).mkdir(parents=True)
    claude = home / ".claude" / "projects" / "-x"
    claude.mkdir(parents=True)
    (claude / "s1.jsonl").write_text(json.dumps({"type": "user", "cwd": str(home / "Projects/alpha")}) + "\n")
    codex = home / ".codex"
    (codex / "sessions" / "2026" / "10" / "16").mkdir(parents=True)
    (codex / "config.toml").write_text(f'[projects."{home}/Projects/beta"]\ntrust_level = "trusted"\n'
                                       f'[projects."/etc"]\ntrust_level = "trusted"\n')
    (codex / "sessions" / "2026" / "10" / "16" / "rollout-2026-10-16T01-00-00-abc.jsonl").write_text(
        json.dumps({"type": "session_meta", "payload": {"cwd": str(home / "Projects/alpha")}}) + "\n")
    agy = home / ".gemini" / "antigravity"
    agy.mkdir(parents=True)
    (agy / "agyhub_summaries_proto.pb").write_bytes(
        b"\x0a\x12" + str(home / "Projects/gamma").encode() + b"\x12\x03xyz" + str(home / "Projects/gone").encode())
    return home


def test_lists_the_folders_the_providers_know(tmp_path):
    home = make_home(tmp_path)
    folders = {f["name"]: f for f in known_folders(home, extra=[str(home / "Projects/lab"), "/tmp"])}
    assert set(folders) == {"alpha", "beta", "gamma", "lab"}  # not /etc, /tmp, or missing folders
    assert folders["alpha"]["providers"] == ["claude-code", "codex"]
    assert folders["beta"]["providers"] == ["codex"]
    assert folders["gamma"]["providers"] == ["antigravity"]
    assert folders["lab"]["providers"] == []
    assert all(f["state"] == "off" for f in folders.values())
    assert list(folders)[0] == "alpha"  # most recently used first


def test_switching_writes_only_the_markers(tmp_path):
    home = make_home(tmp_path)
    beta, sub = home / "Projects/beta", home / "Projects/beta/sub"
    assert set_folder(str(beta), True, home)["state"] == "on"
    assert (beta / ".placet-on").exists()
    assert state_of(sub) == "inherited"
    # Off inside an active project: excluded with .placet-off.
    assert set_folder(str(sub), False, home)["state"] == "excluded"
    assert (sub / ".placet-off").exists()
    assert set_folder(str(sub), True, home)["state"] == "inherited"
    assert not (sub / ".placet-off").exists() and not (sub / ".placet-on").exists()
    assert set_folder(str(beta), False, home)["state"] == "off"
    assert sorted(p.name for p in beta.iterdir()) == ["sub"]


@pytest.mark.parametrize("bad", ["/etc", "/tmp", "~", "{home}", "{home}/../x"])
def test_only_folders_inside_home(tmp_path, bad):
    home = make_home(tmp_path)
    with pytest.raises(FolderError) as e:
        set_folder(bad.format(home=home), True, home)
    assert e.value.code == "invalid"
    with pytest.raises(FolderError) as e:
        set_folder(str(home / "Projects/missing"), True, home)
    assert e.value.code == "not_found"


def test_symlinks_cannot_lead_outside_home(tmp_path):
    home = make_home(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (home / "Projects" / "link").symlink_to(outside)
    with pytest.raises(FolderError):
        set_folder(str(home / "Projects/link"), True, home)
    assert not (outside / ".placet-on").exists()


# --- API --------------------------------------------------------------------------------

CONFIG = PlacetConfig(agents={
    "claude": AgentSpec(tmux_session="agent-claude", token="tok-claude", allowed_targets=["human"]),
})


@pytest.mark.asyncio
async def test_folders_through_the_bridge(tmp_path, monkeypatch):
    home = make_home(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    db = Database(os.path.join(tempfile.mkdtemp(), "placet.db"))
    db.init_db()
    app = create_app(db=db, config=CONFIG, bridge_token="tok-bridge", transcripts_local=False)
    async with agent_client(app, "tok-claude") as claude:
        await claude.post("/api/sessions/s1/events", json={"event": "start", "cwd": str(home / "Projects/lab")})
    daemon = BridgeDaemon(store=object.__new__(HttpStore), tmux=object(), channels={})

    async def bridge(polls):
        async with agent_client(app, "tok-bridge") as client:
            for _ in range(polls):
                resp = await client.get("/api/bridge/jobs?wait_seconds=5")
                for job in resp.json()["jobs"]:
                    result = await asyncio.to_thread(daemon.handle_job, job)
                    await client.post(f"/api/bridge/jobs/{job['id']}/result", json=result)

    task = asyncio.create_task(bridge(3))
    await asyncio.sleep(0.05)
    async with human_client(app) as human:
        listed = (await human.get("/api/folders")).json()["folders"]
        assert "lab" in {f["name"] for f in listed}  # from the registered session
        resp = await human.put("/api/folders", json={"path": str(home / "Projects/lab"), "active": True})
        assert resp.status_code == 200 and resp.json()["state"] == "on"
        resp = await human.put("/api/folders", json={"path": "/etc", "active": True})
        assert resp.status_code == 400
    await task
    assert (home / "Projects/lab/.placet-on").exists()
    async with agent_client(app, "tok-claude") as claude:
        assert (await claude.get("/api/folders")).status_code == 403
        assert (await claude.put("/api/folders", json={"path": "/x", "active": True})).status_code == 403


@pytest.mark.asyncio
async def test_folders_without_the_bridge(tmp_path):
    db = Database(os.path.join(tempfile.mkdtemp(), "placet.db"))
    db.init_db()
    app = create_app(db=db, config=CONFIG, bridge_token="tok-bridge", transcripts_local=False)
    async with human_client(app) as human:
        assert (await human.get("/api/folders")).status_code == 503
