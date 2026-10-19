"""Protected confirmation: real WebAuthn, proposal binding and narrow RPC roles."""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.test_admin_authorization import setup as authorization_setup, assertion
from admin_executor.broker import ConfirmationBroker, BrokerPolicy
from admin_executor.execution import ExecutionStore, Supervisor


@pytest.fixture
def broker(authorization_setup):
    path, store, private, _ = authorization_setup
    policy = BrokerPolicy(enabled=frozenset(('restart-backend', 'restart-bridge')), revision=1)
    service = ConfirmationBroker(store, policy=lambda: policy, return_url='https://placet.example.test/#/comandi')
    return service, private, policy


def proposal(service, key='phone-request'):
    return service.propose(key, 'restart-backend', {})['id']


def test_proposal_is_durable_idempotent_and_description_is_trusted(broker):
    service, _, _ = broker
    identifier = proposal(service)
    assert proposal(service) == identifier
    shown = service.view(identifier)
    assert shown['action'] == 'restart-backend'
    assert shown['state'] == 'awaiting_confirmation'
    assert 'placet-backend.service' in shown['operation']['summary']
    assert shown['return_url'] == 'https://placet.example.test/#/comandi'
    with pytest.raises(ValueError):
        service.propose('phone-request', 'restart-bridge', {})
    with pytest.raises(ValueError):
        service.propose('another', 'restart-backend', {'shell': 'anything'})


def test_authorization_is_one_atomic_job_per_proposal_and_retry_returns_it(broker):
    service, private, _ = broker
    identifier = proposal(service)
    challenge = service.challenge(identifier)
    response = assertion(private, challenge)
    first = service.authorize(identifier, challenge['id'], response)
    again = service.authorize(identifier, challenge['id'], response)
    assert first['job_id'] == again['job_id']
    assert service.challenge(identifier)['job_id'] == first['job_id']
    with sqlite3.connect(service.store.path) as db:
        assert db.execute('SELECT count(*) FROM authorized_jobs').fetchone()[0] == 1
        assert db.execute('SELECT consumed FROM challenges').fetchone()[0] == 1
        assert db.execute('SELECT job_id FROM proposal_jobs').fetchone()[0] == first['job_id']
    calls = []
    Supervisor(ExecutionStore(service.store.path), enabled={'restart-backend'},
               handler=lambda action: calls.append(action) or 'succeeded').run_once()
    shown = service.view(identifier)
    assert shown['state'] == 'succeeded'
    assert calls == ['restart-backend']
    assert 'assertion' not in json.dumps(shown)
    assert 'public_key' not in json.dumps(shown)


def test_concurrent_retry_creates_no_second_job(broker):
    service, private, _ = broker
    identifier = proposal(service)
    challenge = service.challenge(identifier)
    response = assertion(private, challenge)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: service.authorize(identifier, challenge['id'], response), range(2)))
    assert results[0]['job_id'] == results[1]['job_id']


def test_challenge_cannot_be_rebound_to_another_proposal(broker):
    service, private, _ = broker
    one, two = proposal(service), proposal(service, 'other-request')
    challenge = service.challenge(one)
    with pytest.raises(ValueError):
        service.authorize(two, challenge['id'], assertion(private, challenge))
    assert service.view(one)['state'] == 'awaiting_confirmation'


@pytest.mark.parametrize('changes', [{'flags': 1}, {'origin': 'https://placet.example.test'},
                                    {'rp': 'placet.example.test'}, {'cross_origin': True}])
def test_invalid_signature_context_never_creates_job(broker, changes):
    service, private, _ = broker
    identifier = proposal(service)
    challenge = service.challenge(identifier)
    with pytest.raises(Exception):
        service.authorize(identifier, challenge['id'], assertion(private, challenge, **changes))
    assert service.view(identifier)['state'] == 'awaiting_confirmation'


def test_disabled_or_changed_catalogue_invalidates_pending_challenge(broker):
    service, private, _ = broker
    identifier = proposal(service)
    challenge = service.challenge(identifier)
    response = assertion(private, challenge)
    for policy in (BrokerPolicy(enabled=frozenset(), revision=1),
                   BrokerPolicy(enabled=frozenset(('restart-backend',)), revision=2)):
        service.policy = lambda: policy
        with pytest.raises(ValueError):
            service.authorize(identifier, challenge['id'], response)
    with sqlite3.connect(service.store.path) as db:
        assert db.execute('SELECT count(*) FROM authorized_jobs').fetchone()[0] == 0


def test_expired_challenge_and_changed_parameters_are_refused(broker):
    service, private, _ = broker
    identifier = proposal(service)
    challenge = service.challenge(identifier)
    response = assertion(private, challenge)
    with sqlite3.connect(service.store.path) as db:
        db.execute('UPDATE challenges SET expires=0')
    with pytest.raises(ValueError):
        service.authorize(identifier, challenge['id'], response)
    with sqlite3.connect(service.store.path) as db:
        db.execute("UPDATE confirmation_proposals SET canonical='{}'")
    with pytest.raises(ValueError):
        service.challenge(identifier)


def test_rpc_roles_do_not_accept_a_claimed_uid_or_start_method(broker):
    from admin_executor.control import dispatch
    service, _, _ = broker
    peers = {'web': 1001, 'backend': 1002}
    def send(uid, method, **params):
        return dispatch(service, peers=peers, peer_uid=uid, request={'method': method, 'params': params})
    identifier = send(1002, 'propose', request_key='phone', action='restart-backend', parameters={})['id']
    assert send(1001, 'view', identifier=identifier)['state'] == 'awaiting_confirmation'
    for uid, method, params in ((1002, 'challenge', {'identifier': identifier}),
                               (1001, 'propose', {'request_key': 'x', 'action': 'restart-backend', 'parameters': {}}),
                               (1003, 'catalogue', {}), (1001, 'execute', {}),
                               (1002, 'catalogue', {'peer_uid': 1001})):
        with pytest.raises(ValueError):
            send(uid, method, **params)


def test_bridge_status_is_explicitly_reported_by_user_service(broker):
    service, _, _ = broker
    identifier = service.propose('bridge', 'restart-bridge', {})['id']
    assert service.view(identifier)['evidence'] == 'user_service_report'


def test_real_unix_rpc_verifies_server_and_peer_credentials(broker, tmp_path):
    import os
    import threading
    from admin_executor.control import BrokerServer, BrokerClient, BrokerUnavailable
    if os.geteuid() == 0:
        pytest.skip('Unprivileged socket integration')
    service, _, _ = broker
    path = tmp_path / 'web.sock'
    server = BrokerServer(path, service, peers={'web': os.geteuid(), 'backend': os.geteuid() + 1}, role='web')
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = BrokerClient(path, server_uid=os.geteuid())
        assert client.call('catalogue')['revision'] == 1
        with pytest.raises(ValueError):
            client.call('execute')
        with pytest.raises(BrokerUnavailable):
            BrokerClient(path).call('catalogue')
        with pytest.raises(ValueError):
            client.call('propose', request_key='fake', action='restart-backend', parameters={})
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_changed_summary_invalidates_signature_but_saved_job_keeps_original_text(broker, monkeypatch):
    from admin_executor.broker import SUMMARIES
    service, private, _ = broker
    identifier = proposal(service)
    challenge = service.challenge(identifier)
    original = service.view(identifier)['operation']['summary']
    response = assertion(private, challenge)
    monkeypatch.setitem(SUMMARIES, 'restart-backend', 'A changed protected description')
    with pytest.raises(ValueError):
        service.authorize(identifier, challenge['id'], response)
    monkeypatch.setitem(SUMMARIES, 'restart-backend', original)
    service.authorize(identifier, challenge['id'], response)
    monkeypatch.setitem(SUMMARIES, 'restart-backend', 'New description for future proposals')
    assert service.view(identifier)['operation']['summary'] == original


def test_broker_entrypoint_refuses_nonroot(monkeypatch):
    from admin_executor.control import main
    monkeypatch.setattr('os.geteuid', lambda: 1000)
    with pytest.raises(ValueError, match='root'):
        main()


def test_unconfirmed_proposal_expires_after_fifteen_minutes(broker):
    service, private, _ = broker
    identifier = proposal(service)
    challenge = service.challenge(identifier)
    with sqlite3.connect(service.store.path) as db:
        db.execute('UPDATE confirmation_proposals SET created_at=created_at-901 WHERE id=?', (identifier,))
    shown = service.view(identifier)
    assert shown['state'] == 'expired'
    assert shown['enabled'] is False
    with pytest.raises(ValueError):
        service.challenge(identifier)
    with pytest.raises(ValueError):
        service.authorize(identifier, challenge['id'], assertion(private, challenge))
    with sqlite3.connect(service.store.path) as db:
        assert db.execute('SELECT count(*) FROM authorized_jobs').fetchone()[0] == 0


def test_authorized_proposal_does_not_expire_while_its_job_is_waiting(broker):
    service, private, _ = broker
    identifier = proposal(service)
    challenge = service.challenge(identifier)
    first = service.authorize(identifier, challenge['id'], assertion(private, challenge))
    with sqlite3.connect(service.store.path) as db:
        db.execute('UPDATE confirmation_proposals SET created_at=0')
    assert service.view(identifier)['state'] == 'authorized'
    assert service.challenge(identifier)['job_id'] == first['job_id']


def test_proposal_expiring_during_verification_rolls_back_authorization(broker, monkeypatch):
    import time
    import webauthn
    service, private, _ = broker
    identifier = proposal(service)
    now = time.time()
    with sqlite3.connect(service.store.path) as db:
        db.execute('UPDATE confirmation_proposals SET created_at=?', (now - 899,))
    challenge = service.challenge(identifier)
    response = assertion(private, challenge)
    actual = webauthn.verify_authentication_response
    def verify(**kwargs):
        result = actual(**kwargs)
        monkeypatch.setattr('admin_executor.authorization.time.time', lambda: now + 2)
        return result
    monkeypatch.setattr(webauthn, 'verify_authentication_response', verify)
    with pytest.raises(ValueError, match='Proposal expired during verification'):
        service.authorize(identifier, challenge['id'], response)
    with sqlite3.connect(service.store.path) as db:
        assert db.execute('SELECT count(*) FROM authorized_jobs').fetchone()[0] == 0
        assert db.execute('SELECT consumed FROM challenges').fetchone()[0] == 0
