"""Suspended Codex chats: queued once, then opened through the app's native link."""
import pytest

from tests.helpers import agent_client
from tests.test_session_messages import setup, start, write  # noqa: F401

SID = '01a1467c-2982-78a0-8246-61283d1c3e80'


@pytest.mark.asyncio
async def test_suspended_mail_is_offered_for_waking(setup):  # noqa: F811
    app, db = setup
    await start(app, 'codex', SID, 'codex')
    db.suspend_session(SID)
    await write(app, SID, 'test')
    async with agent_client(app, 'tok-bridge') as bridge:
        data = (await bridge.get('/api/bridge/work')).json()
    assert data['sessions'] == []
    assert data['wake_sessions'] == [{'id': SID, 'provider': 'codex', 'suspended': True}]


@pytest.mark.asyncio
async def test_native_wakeup_keeps_suspension_and_one_in_flight(setup):  # noqa: F811
    app, db = setup
    await start(app, 'codex', SID, 'codex')
    db.suspend_session(SID)
    first = (await write(app, SID, 'prima')).json()['id']
    second = (await write(app, SID, 'seconda')).json()['id']
    async with agent_client(app, 'tok-bridge') as bridge:
        taken = await bridge.post(f'/api/bridge/sessions/{SID}/take')
        assert taken.json()['request_id'] == first
        db.confirm_injection(first, actor='bridge', via='test')
        assert db.get_request(first).delivery == 'accepted'
        assert db.get_request(first).execution == 'unobserved'
        assert db.get_session(SID).suspended_at is not None
        assert (await bridge.post(f'/api/bridge/sessions/{SID}/take')).status_code == 204
        assert db.get_request(second).status.value == 'APPROVED'
