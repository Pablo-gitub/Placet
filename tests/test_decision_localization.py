"""Structured decision errors preserve the existing validation and status codes."""
import pytest
from backend.app.models import RequestCreate, RequestStatus
from tests.test_session_messages import setup
from tests.helpers import human_client


@pytest.mark.asyncio
@pytest.mark.parametrize('question_type,options,payload,code', [
    ('confirmation', [], {'edited_prompt': 'edit'}, 'decision.prompt_edit_not_allowed'),
    ('confirmation', [], {'recipient_session_id': 'c1'}, 'decision.recipient_not_allowed'),
    ('confirmation', [], {'selected_options': ['A']}, 'decision.no_options'),
    ('multi_choice', ['A'], {'selected_options': ['A', 'A']}, 'decision.duplicate_options'),
    ('single_choice', ['A'], {'selected_options': ['B']}, 'decision.unknown_options'),
    ('single_choice', ['A'], {'status': 'REJECTED', 'selected_options': ['A']}, 'decision.rejected_options'),
    ('single_choice', ['A'], {}, 'decision.single_choice'),
    ('multi_choice', ['A'], {}, 'decision.multi_choice'),
])
async def test_invalid_decisions_return_codes_without_mutation(setup, question_type, options, payload, code):
    app, db = setup
    req = db.create_request(RequestCreate(sender_id='claude', recipient_id='human',
        prompt='Original question', question_type=question_type, options=options))
    async with human_client(app) as human:
        response = await human.post(f'/api/requests/{req.id}/decision', json={'status': 'APPROVED', **payload})
    assert response.status_code == 422
    assert response.json()['code'] == code and response.json()['params'] == {}
    assert isinstance(response.json()['detail'], str)
    assert db.get_request(req.id).status == RequestStatus.PENDING


@pytest.mark.asyncio
@pytest.mark.parametrize('case,code', [
    ('missing', 'session.unavailable'), ('excluded', 'session.excluded'),
    ('self', 'decision.self_target'), ('now', 'decision.now_target'),
])
async def test_invalid_target_returns_structured_error(setup, case, code):
    app, db = setup
    db.upsert_session('c1', 'claude', provider='claude-code')
    req = db.create_request(RequestCreate(sender_id='claude', recipient_id='claude', prompt='Review'))
    db.link_sender_session(req.id, 'c1', 'claude')
    payload = {'status': 'APPROVED'}
    if case == 'excluded':
        db.upsert_session('c2', 'claude', provider='claude-code', cwd='/excluded')
        db.set_excluded_folders(['/excluded'], ['/excluded'])
        payload['recipient_session_id'] = 'c2'
    elif case == 'now':
        payload['delivery_mode'] = 'now'
    else:
        payload['recipient_session_id'] = 'c1' if case == 'self' else 'missing'
    async with human_client(app) as human:
        response = await human.post(f'/api/requests/{req.id}/decision', json=payload)
    assert response.status_code == 422 and response.json()['code'] == code
    assert db.get_request(req.id).status == RequestStatus.PENDING


@pytest.mark.asyncio
async def test_conflicts_include_request_id_for_localized_feedback(setup):
    app, db = setup
    req = db.create_request(RequestCreate(sender_id='claude', recipient_id='human', prompt='Proceed?'))
    async with human_client(app) as human:
        assert (await human.post(f'/api/requests/{req.id}/decision', json={'status': 'REJECTED'})).status_code == 200
        response = await human.post(f'/api/requests/{req.id}/decision', json={'status': 'APPROVED'})
    assert response.status_code == 409
    assert response.json()['code'] == 'decision.not_pending'
    assert response.json()['params'] == {'id': req.id}
    assert db.get_request(req.id).status == RequestStatus.REJECTED
