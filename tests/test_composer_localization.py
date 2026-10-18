"""Product error codes for phone messages and attachment transport."""
import pytest
from backend.app.database import Database
from backend.app.main import create_app
from bridge.attachments import AttachmentError
from tests.test_session_messages import setup, start, CONFIG
from tests.test_uploads import upload
from tests.helpers import human_client


@pytest.mark.asyncio
@pytest.mark.parametrize('case,code,status', [
    ('missing', 'session.not_found', 404), ('closed', 'session.closed', 409),
    ('missing_mention', 'mention.missing', 422), ('unavailable', 'mention.unavailable', 409),
    ('self', 'mention.self', 422), ('ambiguous', 'mention.ambiguous', 422),
])
async def test_message_codes_do_not_queue_failed_messages(setup, case, code, status):
    app, db = setup
    await start(app, 'claude', 'c1', 'claude-code')
    payload = {'text': '@target'}
    target = 'c1'
    if case == 'missing': target = 'missing'
    elif case == 'closed': db.end_session('c1')
    else:
        db.upsert_session('c2', 'claude', provider='claude-code')
        session_id = 'missing' if case == 'unavailable' else 'c1' if case == 'self' else 'c2'
        payload['mentions'] = [{'session_id': session_id, 'token': '@target' if case != 'missing_mention' else '@absent'}]
        if case == 'ambiguous': payload['mentions'].append({'session_id': 'c1', 'token': '@target'})
    async with human_client(app) as human:
        response = await human.post(f'/api/sessions/{target}/messages', json=payload)
    assert response.status_code == status and response.json()['code'] == code
    assert db.list_requests() == []


@pytest.mark.asyncio
@pytest.mark.parametrize('error,status', [('too_large', 413), ('unsupported', 415), ('forbidden', 403), ('excluded', 409)])
async def test_upload_error_codes_preserve_failure_and_status(tmp_path, monkeypatch, error, status):
    db = Database(str(tmp_path / 'db.sqlite')); db.init_db()
    app = create_app(db=db, config=CONFIG, transcripts_local=True)
    await start(app, 'claude', 'c1', 'claude-code')
    def fail(*args, **kwargs): raise AttachmentError(error, 'Diagnostic only')
    monkeypatch.setattr('bridge.uploads.save_uploads', fail)
    async with human_client(app) as human:
        response = await human.post('/api/sessions/c1/messages', json={'attachments': [upload()]})
    assert response.status_code == status and response.json()['code'] == f'upload.{error}'
    assert db.list_requests() == []


@pytest.mark.asyncio
async def test_invalid_upload_and_offline_bridge_have_codes(tmp_path):
    db = Database(str(tmp_path / 'db.sqlite')); db.init_db()
    app = create_app(db=db, config=CONFIG, transcripts_local=False)
    await start(app, 'claude', 'c1', 'claude-code')
    async with human_client(app) as human:
        invalid = await human.post('/api/sessions/c1/messages', json={'attachments': [upload(b'<html/>')]})
        assert invalid.status_code == 415 and invalid.json()['code'] == 'upload.invalid'
        offline = await human.post('/api/sessions/c1/messages', json={'attachments': [upload()]})
        assert offline.status_code == 503 and offline.json()['code'] == 'bridge.offline'
    assert db.list_requests() == []
