import asyncio
import base64
import json
from pathlib import Path
from unittest.mock import MagicMock
import pytest
from backend.app.main import create_app
from backend.app.database import Database
from backend.app.envelopes import injection
from bridge.attachments import AttachmentError
from bridge.uploads import save_uploads, validate_uploads
from bridge.bridge_daemon import BridgeDaemon
from tests.helpers import human_client, agent_client
from tests.test_session_messages import CONFIG, start
from tests.test_attachments import png
from tests.test_transcript_api import fake_bridge


def upload(data=None, name="foto.png"):
    return {"name": name, "data": base64.b64encode(png() if data is None else data).decode()}


def test_private_storage_and_retry(tmp_path):
    first = save_uploads("../../s", [upload(name="../../foto.png"), upload(b"%PDF-1.4\n%%EOF", "doc.pdf")], home=tmp_path)
    again = save_uploads("../../s", [upload()], home=tmp_path)
    assert first["files"][0]["path"] == again["files"][0]["path"]
    for item in first["files"]:
        path = Path(item["path"])
        assert path.is_relative_to(tmp_path / ".cache/placet/uploads")
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700


def test_validation_and_exclusion(tmp_path, monkeypatch):
    for files in ([upload(b"<script/>")], [upload(b"GIF89a")], [upload()] * 5, [{"name": "x", "data": "!!!!"}]):
        with pytest.raises(AttachmentError): validate_uploads(files)
    monkeypatch.setattr("bridge.uploads.MAX_FILE_BYTES", 5)
    with pytest.raises(AttachmentError): validate_uploads([upload()])
    (tmp_path / ".placet-off").touch()
    with pytest.raises(AttachmentError, match="excluded"):
        save_uploads("s1", [upload()], home=tmp_path, cwd=str(tmp_path))


def test_symlink_rejected(tmp_path):
    target = tmp_path / "elsewhere"; target.mkdir()
    cache = tmp_path / ".cache"; cache.mkdir()
    (cache / "placet").symlink_to(target)
    with pytest.raises(AttachmentError): save_uploads("s1", [upload()], home=tmp_path)
    assert not list(target.iterdir())


@pytest.mark.asyncio
async def test_bridge_saves_before_approval(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "db.sqlite")); db.init_db()
    app = create_app(db=db, config=CONFIG, bridge_token="tok-bridge", transcripts_local=False)
    await start(app, "codex", "cx1", "codex")
    home = tmp_path / "home"; home.mkdir()
    monkeypatch.setattr("bridge.uploads.Path.home", lambda: home)
    daemon = BridgeDaemon(store=MagicMock(), tmux=MagicMock())
    jobs = []
    def handle(job):
        jobs.append(job)
        assert not db.inbox_has_message(db.get_session("cx1"))
        return daemon.handle_job(job)
    async with agent_client(app, "tok-bridge") as bridge:
        await bridge.get("/api/bridge/jobs")
    worker = asyncio.create_task(fake_bridge(app, handle))
    async with human_client(app) as human:
        response = await human.post("/api/sessions/cx1/messages", json={"attachments": [upload()]})
    await worker
    assert response.status_code == 201, response.text
    req = db.get_request(response.json()["id"])
    assert req.status == "APPROVED" and req.recipient_session_id == "cx1"
    assert jobs[0]["kind"] == "upload" and "foto.png" in req.prompt
    metadata = json.loads(req.context.split("\n")[1])
    assert Path(metadata[0]["path"]).read_bytes() == png()
    assert upload()["data"] not in req.model_dump_json()
    assert metadata[0]["path"] in injection(req, channel="app")


@pytest.mark.asyncio
async def test_offline_invalid_unauthorized_do_not_queue(tmp_path):
    db = Database(str(tmp_path / "db.sqlite")); db.init_db()
    app = create_app(db=db, config=CONFIG, bridge_token="tok-bridge", transcripts_local=False)
    await start(app, "codex", "cx1", "codex")
    async with human_client(app) as human:
        for payload, code in [({"attachments": [upload()]}, 503), ({"attachments": [upload(b"<html/>")]}, 415), ({"attachments": []}, 422)]:
            assert (await human.post("/api/sessions/cx1/messages", json=payload)).status_code == code
    async with agent_client(app, "tok-codex") as agent:
        assert (await agent.post("/api/sessions/cx1/messages", json={"attachments": [upload()]})).status_code == 403
    assert not db.inbox_has_message(db.get_session("cx1"))


def test_storage_quota_preserves_retry(tmp_path, monkeypatch):
    first = save_uploads("s1", [upload()], home=tmp_path)
    monkeypatch.setattr("bridge.uploads.MAX_STORED_BYTES", len(png()))
    assert save_uploads("s1", [upload()], home=tmp_path) == first
    with pytest.raises(AttachmentError, match="256 MB"):
        save_uploads("s1", [upload(b"%PDF-1.4\n%%EOF", "doc.pdf")], home=tmp_path)


@pytest.mark.asyncio
async def test_chunked_request_size_is_bounded_before_parsing():
    from backend.app.upload_limits import MessageBodyLimit
    called = False
    async def inner(scope, receive, send):
        nonlocal called
        called = True
    parts = iter([{"type": "http.request", "body": b"12345", "more_body": True},
                  {"type": "http.request", "body": b"67890", "more_body": False}])
    async def receive(): return next(parts)
    sent = []
    async def send(message): sent.append(message)
    await MessageBodyLimit(inner, limit=8)({"type": "http", "method": "POST", "path": "/api/sessions/s1/messages"}, receive, send)
    assert not called
    assert sent[0]["status"] == 413
