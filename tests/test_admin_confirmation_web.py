"""HTTP confirmation cannot bypass the protected broker or origin boundary."""
import httpx
import pytest

from tests.test_admin_broker import broker, authorization_setup, proposal
from tests.test_admin_authorization import assertion
from admin_executor.web import create_app, EnrollmentMailbox
from admin_executor.control import BrokerUnavailable


ORIGIN = 'https://admin.example.test'


@pytest.fixture
def web(broker, tmp_path):
    service, private, _ = broker
    class Client:
        def call(self, method, **params):
            return getattr(service, method)(**params)
    app = create_app(origin=ORIGIN, mailbox=EnrollmentMailbox(tmp_path / 'mailbox.sqlite'), broker=Client())
    return app, service, private


@pytest.mark.asyncio
async def test_confirmation_requires_same_origin_and_returns_durable_result(web):
    app, service, private = web
    identifier = proposal(service)
    base = '/api/proposals/' + identifier
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=ORIGIN) as client:
        assert (await client.get(base)).json()['state'] == 'awaiting_confirmation'
        assert (await client.post(base + '/challenge', json={})).status_code == 403
        headers = {'origin': ORIGIN}
        challenge = (await client.post(base + '/challenge', json={}, headers=headers)).json()
        body = {'challenge_id': challenge['id'], 'credential': assertion(private, challenge)}
        result = await client.post(base + '/authorize', json=body, headers=headers)
        assert result.json()['state'] == 'authorized'
        assert (await client.post(base + '/authorize', json=body, headers=headers)).json()['job_id'] == result.json()['job_id']
        assert (await client.get(base)).json()['job_id'] == result.json()['job_id']
        assert (await client.post(base + '/authorize', json={**body, 'action': 'deploy'}, headers=headers)).status_code == 422
        assert (await client.post(base + '/challenge', json={'summary': 'fake'}, headers=headers)).status_code == 422
        assert (await client.post(base + '/authorize', json=body, headers={'origin': 'https://pwa.example.test'})).status_code == 403
        assert (await client.get('/api/status')).json()['execution_enabled'] is True
        assert (await client.post('/api/execute', json={}, headers=headers)).status_code == 404


@pytest.mark.asyncio
async def test_offline_broker_is_explicit_without_enabling_commands(tmp_path):
    class Offline:
        def call(self, *_args, **_kwargs):
            raise BrokerUnavailable()
    app = create_app(origin=ORIGIN, mailbox=EnrollmentMailbox(tmp_path / 'mailbox.sqlite'), broker=Offline())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=ORIGIN) as client:
        assert (await client.get('/api/status')).json()['execution_enabled'] is False
        response = await client.get('/api/proposals/' + 'a' * 32)
        assert response.status_code == 503
        assert response.json() == {'code': 'administrative_unavailable'}
