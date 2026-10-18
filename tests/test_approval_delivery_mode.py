"""Human chooses when to deliver an agent's pending prompt review."""
import pytest

from backend.app.models import RequestCreate, RequestStatus
from tests.test_session_messages import setup
from tests.helpers import agent_client, human_client


def request(db, recipient='gemini', session='g1', **extra):
    return db.create_request(RequestCreate(sender_id='codex', recipient_id=recipient,
                            recipient_session_id=session, prompt='original prompt',
                            context='Human-only review rationale', **extra))


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['queue', 'now'])
async def test_approved_agent_request_uses_selected_delivery_mode(setup, mode):
    app, db = setup
    db.upsert_session('g1', 'gemini', provider='antigravity')
    db.set_session_state('g1', 'busy')
    req = request(db)
    async with human_client(app) as human:
        resp = await human.post(f'/api/requests/{req.id}/decision', json={
            'status': 'APPROVED', 'delivery_mode': mode, 'edited_prompt': 'approved edited prompt',
        })
    assert resp.status_code == 200
    assert resp.json()['delivery_mode'] == mode
    assert db.get_request(req.id).status == RequestStatus.APPROVED
    async with agent_client(app, 'tok-bridge') as bridge:
        work = (await bridge.get('/api/bridge/work')).json()
        assert [r['request_id'] for r in work['steer']] == ([req.id] if mode == 'now' else [])
        if mode == 'now':
            result = await bridge.post(f'/api/bridge/requests/{req.id}/steer')
            assert result.status_code == 200
            text = result.json()['text']
            assert f'[Placet Request #{req.id} da codex]' in text
            assert 'approved edited prompt' in text
            assert 'original prompt' not in text
            assert 'Human-only review rationale' not in text
            assert 'placet_reply' in text and 'turno in corso' in text
            assert 'dall\'utente' not in text
            assert (await bridge.post(f'/api/bridge/requests/{req.id}/steer')).status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['codex', 'automatic', 'human', 'invalid_mode'])
async def test_invalid_now_decision_does_not_approve_or_change_request(setup, case):
    app, db = setup
    db.upsert_session('g1', 'gemini', provider='antigravity')
    db.upsert_session('x1', 'codex', provider='codex')
    req = (request(db, recipient='codex', session='x1') if case == 'codex' else
           request(db, session=None) if case == 'automatic' else
           request(db, recipient='human', session=None) if case == 'human' else request(db))
    async with human_client(app) as human:
        resp = await human.post(f'/api/requests/{req.id}/decision', json={
            'status': 'APPROVED', 'delivery_mode': 'invalid' if case == 'invalid_mode' else 'now',
        })
    assert resp.status_code == 422
    assert db.get_request(req.id).status == RequestStatus.PENDING
    assert db.get_request(req.id).delivery_mode == 'queue'


@pytest.mark.asyncio
async def test_now_approval_atomically_selects_target_and_falls_back_if_idle(setup):
    app, db = setup
    db.upsert_session('g1', 'gemini', provider='antigravity')
    req = request(db, session=None)
    async with human_client(app) as human:
        r = await human.post(f'/api/requests/{req.id}/decision', json={
            'status': 'APPROVED', 'recipient_session_id': 'g1', 'delivery_mode': 'now',
        })
    assert r.status_code == 200
    assert r.json()['recipient_session_id'] == 'g1'
    assert db.steer_candidates() == []
    assert [s.id for s in db.bridge_sessions_with_mail()] == ['g1']
    assert db.take_inbox_message(db.get_session('g1'))[1].id == req.id
    details = next(e.details for e in db.list_events(req.id) if e.event == 'decided')
    assert details['delivery_mode'] == 'now' and details['recipient_session_id'] == 'g1'
