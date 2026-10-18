"""Stable product error codes for transcript, attachments and delivery controls."""
import pytest
from backend.app.database import Database
from backend.app.main import create_app
from backend.app.models import RequestCreate
from bridge.transcripts import TranscriptError
from bridge.attachments import AttachmentError
from tests.test_session_messages import CONFIG, start
from tests.helpers import human_client


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,error,status', [
    ('transcript','not_found',404), ('transcript','unsupported',422), ('transcript','invalid',400),
    ('transcript','excluded',409), ('attachment','not_found',404), ('attachment','forbidden',403),
    ('attachment','unsupported',415), ('attachment','too_large',413), ('attachment','invalid',400),
])
async def test_reader_errors_have_codes(tmp_path, monkeypatch, kind, error, status):
    db = Database(str(tmp_path / 'db')); db.init_db()
    app = create_app(db=db, config=CONFIG, transcripts_local=True)
    await start(app, 'claude', 'c1', 'claude-code')
    def fail(**kwargs):
        raise (TranscriptError if kind == 'transcript' else AttachmentError)(error, 'private diagnostic')
    monkeypatch.setattr(f'bridge.transcripts.read_{"transcript" if kind == "transcript" else "attachment"}', fail)
    path = 'transcript' if kind == 'transcript' else 'attachments/1:0:0'
    async with human_client(app) as human:
        response = await human.get(f'/api/sessions/c1/{path}')
    assert response.status_code == status
    assert response.json()['code'] == f'{kind}.{error}'
    assert 'private diagnostic' not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['transcript','attachment'])
async def test_unknown_bridge_errors_are_sanitized(tmp_path, monkeypatch, kind):
    db = Database(str(tmp_path / 'db')); db.init_db()
    app = create_app(db=db, config=CONFIG, transcripts_local=False)
    await start(app, 'claude', 'c1', 'claude-code')
    async def fail(*args, **kwargs): return {'ok': False, 'error': 'unexpected', 'message': 'private diagnostic'}
    monkeypatch.setattr('backend.app.main.BridgeJobs.run', fail)
    path = 'transcript' if kind == 'transcript' else 'attachments/1:0:0'
    async with human_client(app) as human:
        response = await human.get(f'/api/sessions/c1/{path}')
    assert response.status_code == 502 and response.json()['code'] == f'{kind}.failed'
    assert 'private diagnostic' not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize('path,code', [('delivery/retry','delivery.retry_unavailable'),
    ('delivery/cancel','delivery.cancel_unavailable'), ('queue/release','delivery.release_unavailable')])
async def test_delivery_conflicts_are_localizable(tmp_path, path, code):
    db = Database(str(tmp_path / 'db')); db.init_db()
    app = create_app(db=db, config=CONFIG)
    req = db.create_request(RequestCreate(sender_id='claude', recipient_id='human', prompt='Original'))
    async with human_client(app) as human:
        response = await human.post(f'/api/requests/{req.id}/{path}', json={'reason': 'finished'} if path == 'queue/release' else None)
    assert response.status_code == 409 and response.json()['code'] == code
    assert db.get_request(req.id).prompt == 'Original'
