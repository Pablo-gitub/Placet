"""Human-only administrative proposals; backend never authorizes or dispatches."""
import pytest

from tests.test_api import test_setup
from tests.test_admin_broker import broker, authorization_setup
from tests.test_admin_authorization import assertion
from tests.helpers import agent_client, human_client
from backend.app.main import create_app
from backend.app.admin_commands import AdminCommands, AdminUnavailable
from admin_executor.execution import Supervisor


@pytest.fixture
def setup(test_setup, broker):
    _, db, config = test_setup
    service, private, _ = broker
    class Adapter:
        calls = []
        def call(self, method, **params):
            self.calls.append(method)
            return getattr(service, method)(**params)
    adapter = Adapter()
    app = create_app(db=db, config=config, bridge_token='bridge-token', admin_commands=adapter)
    return app, adapter, service, private, db, config


@pytest.mark.asyncio
async def test_agents_bridge_and_forged_human_headers_cannot_use_commands(setup):
    app, adapter, *_ = setup
    for token in ('token-claude', 'bridge-token', None):
        async with agent_client(app, token) as client:
            for method, path in (('GET', '/api/admin/catalogue'), ('POST', '/api/admin/proposals'),
                                 ('GET', '/api/admin/proposals/' + 'a' * 32)):
                response = await client.request(method, path, headers={'Tailscale-User-Login': 'paolo@test'},
                                                json={'request_key': 'phone', 'action': 'restart-backend', 'parameters': {}})
                assert response.status_code in (401, 403)
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_human_can_only_propose_and_read_fixed_actions(setup):
    app, adapter, *_ = setup
    body = {'request_key': 'phone', 'action': 'restart-backend', 'parameters': {}}
    async with human_client(app) as client:
        catalogue = await client.get('/api/admin/catalogue')
        assert catalogue.json()['status'] == 'available'
        assert catalogue.headers['cache-control'] == 'no-store'
        first = await client.post('/api/admin/proposals', json=body)
        assert first.status_code == 201
        proposal = first.json()
        assert proposal['confirmation_url'] == 'https://admin.example.test/?proposal=' + proposal['id']
        assert proposal['state'] == 'awaiting_confirmation'
        assert 'operation' not in proposal
        assert (await client.post('/api/admin/proposals', json=body)).json()['id'] == proposal['id']
        assert (await client.get('/api/admin/proposals/' + proposal['id'])).json()['state'] == 'awaiting_confirmation'
        for invalid in ({**body, 'action': 'deploy'}, {**body, 'parameters': {'command': 'anything'}},
                        {**body, 'approved': True}, {**body, 'request_key': '../x'}):
            assert (await client.post('/api/admin/proposals', json=invalid)).status_code == 422
        assert (await client.post('/api/admin/execute', json={})).status_code in (404, 405)
        assert (await client.post('/api/admin/proposals/' + proposal['id'] + '/authorize', json={})).status_code in (404, 405)
    assert set(adapter.calls) <= {'catalogue', 'propose', 'view'}


@pytest.mark.asyncio
async def test_catalogue_unavailable_is_explicit_and_never_offers_actions(test_setup):
    _, db, config = test_setup
    class Offline:
        def call(self, *_args, **_kwargs):
            raise AdminUnavailable()
    app = create_app(db=db, config=config, admin_commands=Offline())
    async with human_client(app) as client:
        response = await client.get('/api/admin/catalogue')
        assert response.json() == {'status': 'not_configured', 'execution_enabled': False, 'actions': []}
        response = await client.post('/api/admin/proposals', json={'request_key': 'phone', 'action': 'restart-backend'})
        assert response.status_code == 503
        assert response.json()['code'] == 'admin.unavailable'


@pytest.mark.asyncio
async def test_saved_status_is_read_after_backend_recreation_without_dispatch(setup):
    app, adapter, service, private, db, config = setup
    async with human_client(app) as client:
        created = (await client.post('/api/admin/proposals', json={'request_key': 'recovery', 'action': 'restart-backend'})).json()
    challenge = service.challenge(created['id'])
    service.authorize(created['id'], challenge['id'], assertion(private, challenge))
    calls = []
    Supervisor(service.execution, enabled={'restart-backend'}, handler=lambda action: calls.append(action) or 'succeeded').run_once()
    restored = create_app(db=db, config=config, admin_commands=adapter)
    async with human_client(restored) as client:
        saved = (await client.get('/api/admin/proposals/' + created['id'])).json()
    assert saved['state'] == 'succeeded'
    assert calls == ['restart-backend']
    assert 'assertion' not in saved and 'credential' not in saved


def test_backend_client_has_no_authorization_methods(tmp_path):
    client = AdminCommands(tmp_path / 'absent.sock')
    for method in ('authorize', 'challenge', 'execute', 'restart-backend'):
        with pytest.raises(ValueError):
            client.call(method)
    with pytest.raises(AdminUnavailable):
        client.call('catalogue')


@pytest.mark.asyncio
async def test_recent_proposals_are_durable_public_references(setup):
    app, _, _, _, db, config = setup
    async with human_client(app) as client:
        proposal = (await client.post('/api/admin/proposals', json={'request_key': 'recent', 'action': 'restart-bridge'})).json()
        catalogue = (await client.get('/api/admin/catalogue')).json()
        assert catalogue['recent'][0]['id'] == proposal['id']
        assert catalogue['recent'][0]['evidence'] == 'user_service_report'
        assert 'operation' not in catalogue['recent'][0]


@pytest.mark.asyncio
async def test_lost_proposal_ack_is_recovered_with_same_key(setup):
    app, adapter, service, _, db, config = setup
    original = adapter.call
    dropped = False
    def lose_ack(method, **params):
        nonlocal dropped
        result = original(method, **params)
        if method == 'propose' and not dropped:
            dropped = True
            raise AdminUnavailable()
        return result
    adapter.call = lose_ack
    body = {'request_key': 'same-after-timeout', 'action': 'restart-backend'}
    async with human_client(app) as client:
        first = await client.post('/api/admin/proposals', json=body)
        assert first.status_code == 503
        second = await client.post('/api/admin/proposals', json=body)
        assert second.status_code == 201
        identifier = second.json()['id']
    import sqlite3
    with sqlite3.connect(service.store.path) as database:
        assert database.execute('SELECT count(*) FROM confirmation_proposals').fetchone()[0] == 1
    assert service.view(identifier)['state'] == 'awaiting_confirmation'


def test_backend_client_rejects_unprivileged_socket_server(broker, tmp_path):
    import os
    import threading
    from admin_executor.control import BrokerServer
    if os.geteuid() == 0:
        pytest.skip('Unprivileged socket integration')
    service, *_ = broker
    path = tmp_path / 'fake.sock'
    server = BrokerServer(path, service, peers={'web': os.geteuid() + 1, 'backend': os.geteuid()}, role='backend')
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(AdminUnavailable):
            AdminCommands(path).call('catalogue')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


@pytest.mark.asyncio
async def test_malformed_catalogue_never_exposes_executable_actions(test_setup):
    _, db, config = test_setup
    class Invalid:
        def call(self, *_args, **_kwargs):
            return {'revision': 1, 'execution_enabled': 'true', 'actions': [{'command': 'anything'}]}
    app = create_app(db=db, config=config, admin_commands=Invalid())
    async with human_client(app) as client:
        result = (await client.get('/api/admin/catalogue')).json()
    assert result['status'] == 'not_configured'
    assert result['actions'] == []


@pytest.mark.asyncio
async def test_policy_rejection_and_expired_status_are_not_execution_failures(setup):
    from backend.app.admin_commands import AdminRejected
    import sqlite3
    app, adapter, service, *_ = setup
    async with human_client(app) as client:
        created = (await client.post('/api/admin/proposals', json={'request_key': 'old', 'action': 'restart-backend'})).json()
        with sqlite3.connect(service.store.path) as db:
            db.execute('UPDATE confirmation_proposals SET created_at=0')
        old = (await client.get('/api/admin/proposals/' + created['id'])).json()
        assert old['state'] == 'expired' and old['job_id'] is None
        def reject(*_args, **_kwargs):
            raise AdminRejected()
        adapter.call = reject
        rejected = await client.post('/api/admin/proposals', json={'request_key': 'disabled', 'action': 'restart-backend'})
        assert rejected.status_code == 409
        assert rejected.json()['code'] == 'admin.rejected'
        assert rejected.headers['cache-control'] == 'no-store'


@pytest.mark.asyncio
async def test_invalid_recent_entry_does_not_hide_valid_actions(setup):
    app, adapter, service, *_ = setup
    valid = service.propose('valid', 'restart-backend', {})
    original = adapter.call
    def with_old_entry(method, **params):
        result = original(method, **params)
        if method == 'catalogue':
            result['recent'] = [{'action': 'removed'}, valid]
        return result
    adapter.call = with_old_entry
    async with human_client(app) as client:
        result = (await client.get('/api/admin/catalogue')).json()
    assert result['status'] == 'available'
    assert len(result['actions']) == 2
    assert result['recent'][0]['id'] == valid['id']
