"""Persistent human-only language preferences and explicit localized delivery."""
import json
from string import Formatter
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from backend.app.database import Database
from backend.app.i18n import text, _CATALOGS
from backend.app.models import RequestCreate, RequestModel, RequestStatus, QuestionType
from backend.app.notifier import NtfyConfig, build_notification, build_turn_end
from backend.app import envelopes
from bridge.stores import LocalStore, HttpStore
from bridge.bridge_daemon import BridgeDaemon
from bridge.transcripts import _placet_or_user
from tests.test_session_messages import setup, CONFIG, start
from tests.helpers import agent_client, human_client


def request():
    return RequestModel(id=7, sender_id='claude', recipient_id='gemini', question_type=QuestionType.PROMPT_REVIEW,
        prompt='Original {braces} è unchanged', context='Human-only context', created_at=datetime.now(timezone.utc))


def test_catalog_keys_placeholders_and_default_fallback(monkeypatch, tmp_path):
    monkeypatch.delenv('PLACET_DEFAULT_LANGUAGE', raising=False)
    db = Database(str(tmp_path/'db')); db.init_db()
    assert db.get_background_language() == 'en'
    assert text('notify.open') == 'Open'
    assert text('notify.open', 'unsupported') == 'Open'
    assert _CATALOGS['en'].keys() == _CATALOGS['it'].keys()
    for key in _CATALOGS['en']:
        fields = lambda value: sorted(field for _,field,_,_ in Formatter().parse(value) if field)
        assert fields(_CATALOGS['en'][key]) == fields(_CATALOGS['it'][key]), key
    monkeypatch.delitem(_CATALOGS['it'], 'notify.open')
    assert text('notify.open', 'it') == 'Open'


@pytest.mark.asyncio
async def test_only_human_can_read_or_write_and_setting_survives_restart(setup):
    app, db = setup
    async with human_client(app) as human:
        assert (await human.put('/api/settings/language', json={'language': 'en'})).json() == {'language':'en'}
        read = await human.get('/api/settings/language')
        assert read.json() == {'language':'en'} and read.headers['cache-control'] == 'no-store'
        assert (await human.put('/api/settings/language', json={'language': 'auto'})).status_code == 422
    for token in ['tok-claude', 'tok-bridge']:
        async with agent_client(app, token) as caller:
            assert (await caller.get('/api/settings/language')).status_code == 403
            assert (await caller.put('/api/settings/language', json={'language': 'it'})).status_code == 403
    async with agent_client(app) as caller:
        assert (await caller.put('/api/settings/language', json={'language':'it'}, headers={'Tailscale-User-Login':'paolo@test'})).status_code == 401
    reloaded = Database(db.db_path); reloaded.init_db()
    assert reloaded.get_background_language() == 'en'


@pytest.mark.parametrize('language', ['en', 'it'])
def test_notifications_and_envelopes_preserve_content_and_privacy(language):
    req = request()
    cfg = NtfyConfig(server_url='https://ntfy.sh', topic='test', base_url='https://example.test', language=language)
    _, headers, body = build_notification(req, cfg)
    assert req.prompt not in body and req.context not in body
    assert ('approval needed' if language=='en' else 'da approvare') in headers['Title']
    _, _, private = build_notification(req, replace(cfg, server_url='https://private.example.test'))
    assert req.prompt in private and req.context in private
    _, _, ended = build_turn_end('c1', 'Original title', None, cfg)
    assert ended == text('notify.turn_body', language)
    injection = envelopes.injection(req, language=language)
    assert req.prompt in injection and req.context not in injection
    assert 'placet_ack(request_id=7)' in injection and 'placet_reply(request_id=7' in injection
    assert ('from claude' if language=='en' else 'da claude') in injection


@pytest.mark.parametrize('language', ['en','it'])
def test_hook_user_messages_round_trip_without_translating_prompt(language):
    req = request().model_copy(update={'question_type':QuestionType.MESSAGE, 'prompt':'Original user text è {literal}'})
    message = envelopes.injection(req, channel='hook', language=language)
    parsed = _placet_or_user(message, None)
    assert parsed['role'] == 'user' and parsed['text'] == req.prompt and parsed['request_id'] == req.id
    assert 'placet_reply(' not in message
    assert text('envelope.hook',language) in message


@pytest.mark.asyncio
async def test_backend_and_local_bridge_use_saved_language(setup):
    app, db = setup
    db.set_background_language('en')
    await start(app, 'codex', 'x1', 'codex')
    async with human_client(app) as human:
        created = await human.post('/api/sessions/x1/messages',json={'text':'Original Italian text'})
        assert created.status_code == 201
    async with agent_client(app,'tok-bridge') as bridge:
        work = (await bridge.get('/api/bridge/work')).json()
        assert work['language'] == 'en'
        message = (await bridge.post('/api/bridge/sessions/x1/take')).json()['text']
        assert 'from user' in message and 'Original Italian text' in message
    db.set_background_language('it')
    store = LocalStore(db,CONFIG)
    assert store.work().language == 'it'
    req = db.create_request(RequestCreate(sender_id='claude',recipient_id='gemini',prompt='Original prompt'))
    db.update_request_decision(req.id,RequestStatus.APPROVED)
    daemon = BridgeDaemon(db=db,config=CONFIG,tmux=MagicMock())
    daemon.run_loop_step()
    assert daemon.language == 'it'
    assert 'da claude' in daemon.format_injection_envelope(req)
    daemon.language = 'en'
    assert 'from claude' in daemon.format_injection_envelope(req)


@pytest.mark.asyncio
async def test_generated_user_context_uses_language_without_changing_mention_ids(setup):
    app, db = setup
    db.set_background_language('en')
    await start(app,'claude','c1','claude-code'); await start(app,'codex','x1','codex')
    async with human_client(app) as human:
        result = await human.post('/api/sessions/c1/messages',json={'text':'Original @target', 'mentions':[{'session_id':'x1','token':'@target'}]})
    assert result.status_code == 201
    body=result.json()
    assert body['prompt']=='Original @target'
    assert body['context'].startswith('Session references selected by the user')
    assert '"target_session": "x1"' in body['context']


@pytest.mark.asyncio
async def test_notifications_capture_saved_language_without_browser_or_agent_locale(setup, monkeypatch):
    app, db = setup
    db.set_background_language('en')
    captured = []
    async def notification(request, cfg, origin=None):
        captured.append((cfg.language, build_notification(request, cfg)[1]['Title']))
        return True
    monkeypatch.setattr('backend.app.main.send_ntfy_notification', notification)
    async with agent_client(app,'tok-claude') as agent:
        result = await agent.post('/api/requests',json={'recipient_id':'human','prompt':'Domanda originale','context':'Original context'},headers={'Accept-Language':'it'})
    assert result.status_code == 201 and captured[0][0] == 'en'
    assert captured[0][1].endswith(': question')


def test_http_work_language_is_preserved_without_exposing_settings_write_access():
    store = object.__new__(HttpStore)
    store.client = MagicMock()
    store.client.get.return_value.json.return_value = {'language':'en','injections':[],'results':[]}
    assert store.work().language == 'en'
