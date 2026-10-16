"""Images in the conversations: recognised per provider, served by id only (RF-30)."""
import base64
import json
import os
import struct
import zlib
from pathlib import Path

import pytest

from bridge import attachments as att
from bridge.attachments import AttachmentError
from bridge.transcripts import TranscriptError, read_attachment, read_transcript

SID = "c81e0119-184e-4609-9f5e-f72833fbe159"


def png(w=2, h=1) -> bytes:
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def jpeg(w=3, h=2) -> bytes:
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, h, w, 1) + b"\x01\x11\x00"
    return b"\xff\xd8\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9 + sof + b"\xff\xd9"


def write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def ids(page):
    return [(m["role"], m["text"], [a["id"] for a in m.get("attachments", [])]) for m in page["messages"]]


# --- Content checks --------------------------------------------------------------------


def test_only_real_jpeg_png_webp_of_sane_size():
    assert att.check_image(png()) == "image/png"
    assert att.check_image(jpeg()) == "image/jpeg"
    webp = b"RIFF\x00\x00\x00\x00WEBPVP8L" + b"\x00" * 5 + (9 | (4 << 14)).to_bytes(4, "little")
    assert att.dimensions(webp, "image/webp") == (10, 5)
    for bad in (b"<svg xmlns='http://www.w3.org/2000/svg'/>", b"<html></html>", b"GIF89a....", b""):
        with pytest.raises(AttachmentError):
            att.check_image(bad)
    with pytest.raises(AttachmentError):
        att.check_image(png(w=10000, h=10000))  # 100 Mpx, even if small on disk


# --- Claude: images inside the conversation -------------------------------------------


def test_claude_pasted_image(tmp_path):
    data = base64.b64encode(png()).decode()
    rows = [{"type": "user", "timestamp": "t", "message": {"role": "user", "content": [
        {"type": "text", "text": "guarda"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}}]}}]
    write_jsonl(tmp_path / ".claude" / "projects" / "-p" / f"{SID}.jsonl", rows)
    page = read_transcript("claude-code", SID, home=tmp_path)
    assert ids(page) == [("user", "guarda", ["0:0:0"])]
    assert data not in json.dumps(page)  # the page carries ids, not images
    assert read_attachment("claude-code", SID, "0:0:0", home=tmp_path) == ("image/png", png())


# --- Antigravity: uploads and generated images in brain/<id> ---------------------------


def agy_home(tmp_path, rows):
    brain = tmp_path / ".gemini" / "antigravity" / "brain" / SID
    write_jsonl(brain / ".system_generated" / "logs" / "transcript.jsonl", rows)
    return brain


def test_antigravity_uploaded_and_generated_images(tmp_path):
    brain = tmp_path / ".gemini" / "antigravity" / "brain" / SID
    (brain / ".user_uploaded").mkdir(parents=True)
    (brain / ".user_uploaded" / "media_1.png").write_bytes(png())
    (brain / "tramonto_1.jpg").write_bytes(jpeg())
    agy_home(tmp_path, [
        {"type": "USER_INPUT", "created_at": "t", "content":
            "<USER_REQUEST>\nguarda\n</USER_REQUEST>\n<ADDITIONAL_METADATA>\nThe user has uploaded 1 image(s):\n"
            f"- {brain}/.user_uploaded/media_1.png\nYou can embed this image...\n</ADDITIONAL_METADATA>"},
        {"type": "GENERIC", "created_at": "t", "content":
            f"Using prompt: x\n\nGenerated image is saved at {brain}/tramonto_1.jpg.\n\n Do not output the path"},
        {"type": "PLANNER_RESPONSE", "created_at": "t", "content": "Leggi /home/x/.ssh/id_rsa.png"},
    ])
    page = read_transcript("antigravity", SID, home=tmp_path)
    rows = ids(page)
    assert rows[0][:2] == ("user", "guarda") and len(rows[0][2]) == 1
    assert rows[1][:2] == ("media", "Immagine generata") and len(rows[1][2]) == 1
    assert rows[2][2] == []  # a path written in a message stays text
    assert str(brain) not in json.dumps(page)
    assert read_attachment("antigravity", SID, rows[0][2][0], home=tmp_path) == ("image/png", png())
    assert read_attachment("antigravity", SID, rows[1][2][0], home=tmp_path)[0] == "image/jpeg"


def test_antigravity_refuses_files_outside_the_conversation(tmp_path):
    secret = tmp_path / "secret.png"
    secret.write_bytes(png())
    brain = tmp_path / ".gemini" / "antigravity" / "brain" / SID
    brain.mkdir(parents=True)
    (brain / "link.png").symlink_to(secret)
    other = tmp_path / ".gemini" / "antigravity" / "brain" / "other-conversation"
    other.mkdir()
    (other / "x.png").write_bytes(png())
    agy_home(tmp_path, [
        {"type": "GENERIC", "content": f"Generated image is saved at {secret}."},
        {"type": "GENERIC", "content": f"Generated image is saved at {brain}/link.png."},
        {"type": "GENERIC", "content": f"Generated image is saved at {brain}/../other-conversation/x.png."},
        {"type": "GENERIC", "content": f"Generated image is saved at {brain}/gone.png."},
    ])
    page = read_transcript("antigravity", SID, home=tmp_path)
    found = [m["attachments"][0]["id"] for m in page["messages"]]
    for attachment_id in found[:3]:
        with pytest.raises(AttachmentError) as e:
            read_attachment("antigravity", SID, attachment_id, home=tmp_path)
        assert e.value.code == "forbidden"
    with pytest.raises(AttachmentError) as e:
        read_attachment("antigravity", SID, found[3], home=tmp_path)
    assert e.value.code == "not_found"


def test_ids_cannot_point_anywhere(tmp_path):
    agy_home(tmp_path, [{"type": "PLANNER_RESPONSE", "content": "ciao"}])
    for bad in ("../../etc/passwd", "1:0:0", "0:0:0", "abc", "5:0:0"):
        with pytest.raises((TranscriptError, AttachmentError)):
            read_attachment("antigravity", SID, bad, home=tmp_path)


# --- Codex: clipboard images in /tmp ---------------------------------------------------


def test_codex_pasted_image_and_its_clipboard_file(tmp_path, monkeypatch):
    path = "/tmp/codex-clipboard-b2988e76-057d-4b10-a47e-d3f492543c49.png"
    rows = [{"type": "event_msg", "timestamp": "t", "payload": {"type": "item_completed", "item": {
        "type": "UserMessage", "content": [
            {"type": "text", "text": f"\n# Files mentioned by the user:\n\n## x.png: {path}\n\n## My request:\nguarda qui"},
            {"type": "local_image", "path": path}]}}}]
    write_jsonl(tmp_path / ".codex" / "sessions" / "2026" / f"rollout-2026-10-16T22-00-00-{SID}.jsonl", rows)
    page = read_transcript("codex", SID, home=tmp_path)
    assert ids(page) == [("user", "guarda qui", ["0:0:0"])]
    real_open = open

    def fake_stat(p, follow_symlinks=True):
        return os.stat_result((0o100600, 0, 0, 1, os.getuid(), 0, len(png()), 0, 0, 0))
    monkeypatch.setattr(att.os, "stat", fake_stat)
    monkeypatch.setattr(att.os.path, "isfile", lambda p: True)
    monkeypatch.setattr(att.os.path, "islink", lambda p: False)
    monkeypatch.setattr(att.os.path, "realpath", lambda p: p)
    monkeypatch.setattr("builtins.open", lambda p, *a, **k: __import__("io").BytesIO(png())
                        if str(p) == path else real_open(p, *a, **k))
    assert read_attachment("codex", SID, "0:0:0", home=tmp_path) == ("image/png", png())
    with pytest.raises(AttachmentError):
        att.load("codex", SID, att.file("/tmp/other.png"), home=tmp_path)


# --- Through the backend: by id, through the bridge, never cached ---------------------

from tests.test_transcript_api import SESSION, fake_bridge, register  # noqa: E402
from tests.test_transcript_api import setup as api_setup  # noqa: E402,F401
from tests.helpers import agent_client, human_client  # noqa: E402
import asyncio  # noqa: E402


@pytest.mark.asyncio
async def test_the_pwa_gets_an_image_by_id(api_setup):  # noqa: F811
    app, db = api_setup
    await register(app)
    seen = []

    def handle(job):
        seen.append(job)
        return {"ok": True, "page": {"mime": "image/png", "data": base64.b64encode(png()).decode()}}

    bridge = asyncio.create_task(fake_bridge(app, handle))
    await asyncio.sleep(0.05)
    async with human_client(app) as human:
        resp = await human.get(f"/api/sessions/{SESSION}/attachments/120:0:1")
    await bridge
    assert resp.status_code == 200 and resp.content == png()
    assert resp.headers["content-type"] == "image/png"
    assert resp.headers["cache-control"] == "no-store"
    assert resp.headers["x-content-type-options"] == "nosniff"
    job = seen[0]
    assert job["kind"] == "attachment" and job["attachment_id"] == "120:0:1" and job["cwd"] == "/p"


@pytest.mark.asyncio
async def test_bad_ids_errors_and_rights(api_setup):  # noqa: F811
    app, db = api_setup
    await register(app)
    async with human_client(app) as human:
        assert (await human.get(f"/api/sessions/{SESSION}/attachments/..%2F..%2Fetc")).status_code in (404, 422)
        assert (await human.get(f"/api/sessions/{SESSION}/attachments/1:2")).status_code == 422
    async with agent_client(app, "tok-claude") as claude:
        assert (await claude.get(f"/api/sessions/{SESSION}/attachments/1:0:0")).status_code == 403
    for code, status in [("not_found", 404), ("forbidden", 403), ("unsupported", 415), ("too_large", 413)]:
        bridge = asyncio.create_task(fake_bridge(app, lambda job: {"ok": False, "error": code, "message": "x"}))
        await asyncio.sleep(0.05)
        async with human_client(app) as human:
            resp = await human.get(f"/api/sessions/{SESSION}/attachments/1:0:0")
        await bridge
        assert resp.status_code == status
    # A bridge answer that is not an allowed image is refused too.
    bridge = asyncio.create_task(fake_bridge(app, lambda job: {"ok": True, "page": {
        "mime": "image/svg+xml", "data": base64.b64encode(b"<svg/>").decode()}}))
    await asyncio.sleep(0.05)
    async with human_client(app) as human:
        assert (await human.get(f"/api/sessions/{SESSION}/attachments/1:0:0")).status_code == 415
    await bridge
    db.set_excluded_folders(["/p"], ["/p"])
    async with human_client(app) as human:
        assert (await human.get(f"/api/sessions/{SESSION}/attachments/1:0:0")).status_code == 409


def test_the_bridge_job_reads_and_honours_placet_off(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    from bridge.bridge_daemon import BridgeDaemon
    monkeypatch.setattr("bridge.bridge_daemon.read_attachment", lambda *a, **k: ("image/png", png()))
    daemon = BridgeDaemon(store=MagicMock(), tmux=MagicMock(), channels={})
    job = {"id": "j", "kind": "attachment", "provider": "antigravity", "session_id": SID,
           "attachment_id": "0:0:0", "cwd": str(tmp_path)}
    result = daemon.handle_job(job)
    assert result["ok"] and base64.b64decode(result["page"]["data"]) == png()
    (tmp_path / ".placet-off").touch()
    assert daemon.handle_job(job)["error"] == "excluded"


# --- Files an agent shares with placet_share_file -------------------------------------

PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def shared_home(tmp_path):
    (tmp_path / "Projects" / "app" / "out").mkdir(parents=True)
    (tmp_path / "Projects" / "app" / "out" / "report.pdf").write_bytes(PDF)
    (tmp_path / "Downloads").mkdir()
    (tmp_path / "Downloads" / "grafico.png").write_bytes(png())
    (tmp_path / ".ssh").mkdir()
    (tmp_path / ".ssh" / "key.pdf").write_bytes(PDF)
    (tmp_path / "Projects" / "app" / "notes.txt").write_text("ciao")
    return tmp_path


def test_shared_file_rules(tmp_path):
    home = shared_home(tmp_path)
    assert att.check_shared(str(home / "Projects/app/out/report.pdf"), home).name == "report.pdf"
    assert att.check_shared(str(home / "Downloads/grafico.png"), home)
    (home / "Projects" / "link.pdf").symlink_to(home / ".ssh" / "key.pdf")
    outside = tmp_path.parent / "fuori.pdf"
    outside.write_bytes(PDF)
    for bad in (str(home / ".ssh/key.pdf"), str(home / "Projects/link.pdf"), str(outside),
                "Projects/app/out/report.pdf", str(home), str(home / "Projects/app/../../.ssh/key.pdf")):
        with pytest.raises(AttachmentError):
            att.check_shared(bad, home)
    (home / "Projects" / "app" / ".placet-off").touch()
    with pytest.raises(AttachmentError):
        att.check_shared(str(home / "Projects/app/out/report.pdf"), home)


def test_only_pdfs_and_images_are_served(tmp_path):
    home = shared_home(tmp_path)
    assert att.load("codex", SID, att.shared(str(home / "Projects/app/out/report.pdf")), home) == (
        "application/pdf", PDF)
    with pytest.raises(AttachmentError) as e:
        att.load("codex", SID, att.shared(str(home / "Projects/app/notes.txt")), home)
    assert e.value.code == "unsupported"


def test_each_provider_records_the_share_call(tmp_path):
    home = shared_home(tmp_path)
    path = str(home / "Projects/app/out/report.pdf")
    # Claude
    write_jsonl(home / ".claude" / "projects" / "-p" / f"{SID}.jsonl", [{"type": "assistant", "timestamp": "t",
        "message": {"content": [{"type": "tool_use", "name": "mcp__placet__placet_share_file",
                                 "input": {"path": path, "title": "Rapporto"}}]}}])
    page = read_transcript("claude-code", SID, home=home)
    media = [m for m in page["messages"] if m["role"] == "media"][0]
    assert media["text"] == "File condiviso" and media["attachments"][0]["name"] == "Rapporto"
    assert media["attachments"][0]["kind"] == "pdf" and path not in json.dumps(page)
    assert read_attachment("claude-code", SID, media["attachments"][0]["id"], home=home)[0] == "application/pdf"
    # Codex
    write_jsonl(home / ".codex" / "sessions" / f"rollout-2026-10-17T00-00-00-{SID}.jsonl", [
        {"type": "event_msg", "timestamp": "t", "payload": {"type": "item_completed", "item": {
            "type": "McpToolCall", "server": "placet", "tool": "placet_share_file", "arguments": {"path": path}}}}])
    page = read_transcript("codex", SID, home=home)
    assert [m["role"] for m in page["messages"]] == ["action", "media"]
    # Antigravity: arguments are a JSON string
    agy_home(home, [{"type": "PLANNER_RESPONSE", "created_at": "t", "tool_calls": [{"name": "call_mcp_tool", "args": {
        "ServerName": '"placet"', "ToolName": '"placet_share_file"', "Arguments": json.dumps({"path": path})}}]}])
    page = read_transcript("antigravity", SID, home=home)
    assert page["messages"][-1]["attachments"][0]["kind"] == "pdf"
    # Another tool with a "path" argument is not a share.
    write_jsonl(home / ".claude" / "projects" / "-p" / f"{SID}.jsonl", [{"type": "assistant", "timestamp": "t",
        "message": {"content": [{"type": "tool_use", "name": "mcp__other__read", "input": {"path": path}}]}}])
    assert all(m["role"] != "media" for m in read_transcript("claude-code", SID, home=home)["messages"])


def test_the_mcp_tool_answers_the_agent_at_once(tmp_path, monkeypatch):
    home = shared_home(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    from mcp_server.server import placet_share_file
    assert "File condiviso" in placet_share_file(str(home / "Projects/app/out/report.pdf"), "Rapporto")
    assert placet_share_file(str(home / ".ssh/key.pdf")).startswith("File non condiviso")
    assert placet_share_file(str(home / "Projects/app/notes.txt")).startswith("File non condiviso")


@pytest.mark.asyncio
async def test_pdfs_are_served_inline_without_the_image_sandbox(api_setup):  # noqa: F811
    app, db = api_setup
    await register(app)
    bridge = asyncio.create_task(fake_bridge(app, lambda job: {"ok": True, "page": {
        "mime": "application/pdf", "data": base64.b64encode(PDF).decode()}}))
    await asyncio.sleep(0.05)
    async with human_client(app) as human:
        resp = await human.get(f"/api/sessions/{SESSION}/attachments/1:0:0")
    await bridge
    assert resp.status_code == 200 and resp.headers["content-type"] == "application/pdf"
    assert resp.headers["content-disposition"] == "inline" and resp.headers["cache-control"] == "no-store"
