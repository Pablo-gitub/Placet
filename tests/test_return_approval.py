"""A completed reply is visible to the human, but held from sender and delivery."""
from datetime import datetime, timedelta, timezone
import pytest
from backend.app.models import RequestCreate, RequestStatus
from backend.app.envelopes import outcome
from tests.test_session_messages import setup
from tests.helpers import agent_client, human_client


def completed(db, auto=False, implicit=False):
    db.upsert_session('c1', 'claude', provider='claude-code')
    req = db.create_request(RequestCreate(sender_id='claude', recipient_id='gemini', prompt='review'))
    db.link_sender_session(req.id, 'c1', 'claude')
    db.update_request_decision(req.id, RequestStatus.APPROVED, allow_auto_return=auto)
    return db.record_reply(req.id, 'gemini', 'original secret response', implicit=implicit)


def results(db):
    now = datetime.now(timezone.utc)
    return db.list_undelivered_results(now + timedelta(seconds=1), now - timedelta(days=1))


@pytest.mark.asyncio
@pytest.mark.parametrize('implicit', [False, True])
async def test_return_requires_human_across_all_delivery_channels(setup, implicit):
    app, db = setup
    req = completed(db, implicit=implicit)
    assert not results(db)
    assert db.take_inbox_message(db.get_session('c1')) is None
    assert not db.mark_delivered(req.id)
    async with agent_client(app, 'tok-claude') as sender:
        response = (await sender.get(f'/api/requests/{req.id}')).json()
        assert response['status'] == 'COMPLETED' and response['response'] is None
        assert (await sender.post(f'/api/requests/{req.id}/return-decision', json={'approve': True})).status_code == 403
    async with human_client(app) as human:
        assert (await human.get(f'/api/requests/{req.id}')).json()['response']['content'] == 'original secret response'
        approved = await human.post(f'/api/requests/{req.id}/return-decision', json={'approve': True, 'edited_response': 'human edited'})
        assert approved.status_code == 200
        assert (await human.post(f'/api/requests/{req.id}/return-decision', json={'approve': False})).status_code == 409
    assert len(results(db)) == 1
    async with agent_client(app, 'tok-claude') as sender:
        visible = (await sender.get(f'/api/requests/{req.id}')).json()
        assert visible['response']['content'] == 'human edited'
    assert db.get_request(req.id).response.content == 'original secret response'
    assert db.get_request(req.id).delivered_at is not None
    assert not results(db)


@pytest.mark.asyncio
async def test_reject_return_keeps_completion_and_original_for_human(setup):
    app, db = setup
    req = completed(db)
    async with human_client(app) as human:
        assert (await human.post(f'/api/requests/{req.id}/return-decision', json={'approve': False})).status_code == 200
    assert db.get_request(req.id).status == RequestStatus.COMPLETED
    assert not results(db) and not db.mark_delivered(req.id)
    assert db.take_inbox_message(db.get_session('c1')) is None
    async with agent_client(app, 'tok-claude') as sender:
        assert (await sender.get(f'/api/requests/{req.id}')).json()['response'] is None


@pytest.mark.asyncio
async def test_explicit_auto_return_and_edited_inbox_delivery(setup):
    app, db = setup
    req = completed(db, auto=True)
    assert len(results(db)) == 1
    assert db.take_inbox_message(db.get_session('c1'))[0] == 'outcome'
    other = completed(db)
    assert db.decide_return(other.id, True, 'only authorized text', 'human:test')
    kind, authorized = db.take_inbox_message(db.get_session('c1'))
    text = outcome(authorized, 'app')
    assert kind == 'outcome' and 'only authorized text' in text and 'original secret response' not in text


@pytest.mark.asyncio
async def test_human_can_preapprove_return_for_one_request_and_migration_preserves_it(setup):
    app, db = setup
    req = db.create_request(RequestCreate(sender_id='claude', recipient_id='gemini', prompt='review'))
    async with human_client(app) as human:
        approved = await human.post(f'/api/requests/{req.id}/decision', json={
            'status': 'APPROVED', 'allow_auto_return': True})
        assert approved.status_code == 200 and approved.json()['allow_auto_return']
    db.record_reply(req.id, 'gemini', 'reply')
    db.init_db()
    assert db.get_request(req.id).allow_auto_return
    async with agent_client(app, 'tok-claude') as sender:
        assert (await sender.get(f'/api/requests/{req.id}')).json()['response']['content'] == 'reply'
    held = completed(db)
    db.init_db()
    assert not db.get_request(held.id).allow_auto_return
    assert not db.mark_delivered(held.id)
