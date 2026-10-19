"""Protected intent/confirmation broker; never dispatches a service restart.

The HTTP and backend processes receive only public descriptions, challenges
and result codes. Only this administrative process opens the credential store.
"""
from contextlib import closing
from dataclasses import dataclass
import json
import re
import sqlite3
import time
import uuid
from urllib.parse import urlsplit

from .authorization import confirmation
from .execution import ACTIONS, ExecutionStore
from .jobs import canonical_proposal


SUMMARIES = {
    'restart-backend': 'Restart placet-backend.service. Placet will be temporarily unavailable.',
    'restart-bridge': 'Request restart of placet-bridge.service in the configured desktop user manager.',
}


@dataclass(frozen=True)
class BrokerPolicy:
    enabled: frozenset
    revision: int


class ConfirmationBroker:
    def __init__(self, store, *, policy, return_url):
        parsed = urlsplit(return_url)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.hostname == store.rp_id
                or parsed.username or parsed.password or parsed.port not in (None, 443) or parsed.query):
            raise ValueError('A fixed separate HTTPS return origin is required')
        self.store, self.policy, self.return_url = store, policy, return_url
        self.execution = ExecutionStore(store.path)

    def _policy(self):
        policy = self.policy()
        if (type(policy.revision) is not int or policy.revision != 1
                or not isinstance(policy.enabled, frozenset) or not policy.enabled <= ACTIONS):
            raise ValueError('Unsupported protected catalogue')
        return policy

    def catalogue(self):
        policy = self._policy()
        return {'revision': policy.revision, 'execution_enabled': bool(policy.enabled),
                'actions': [{'id': action, 'summary': SUMMARIES[action], 'parameters': {},
                             'enabled': action in policy.enabled} for action in sorted(ACTIONS)]}

    def propose(self, request_key, action, parameters):
        if not isinstance(request_key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', request_key):
            raise ValueError('Invalid request key')
        if not isinstance(action, str) or action not in self._policy().enabled:
            raise ValueError('Action is not enabled')
        canonical = canonical_proposal(action, parameters)
        with closing(sqlite3.connect(self.store.path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT id,canonical FROM confirmation_proposals WHERE request_key=?', (request_key,)).fetchone()
            if existing:
                if existing[1] != canonical:
                    raise ValueError('Request key already belongs to another proposal')
                identifier = existing[0]
            else:
                identifier = uuid.uuid4().hex
                db.execute('INSERT INTO confirmation_proposals VALUES (?,?,?,?)',
                           (identifier, request_key, canonical, time.time()))
        return self.view(identifier)

    def _proposal(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r'[a-f0-9]{32}', identifier):
            raise ValueError('Invalid proposal ID')
        with closing(sqlite3.connect(self.store.path)) as db:
            row = db.execute('SELECT canonical FROM confirmation_proposals WHERE id=?', (identifier,)).fetchone()
        if row is None:
            raise ValueError('Unknown proposal')
        try:
            data = json.loads(row[0])
            action, parameters = data['action'], data['parameters']
            if action not in ACTIONS or canonical_proposal(action, parameters) != row[0]:
                raise ValueError('Changed protected proposal')
        except (KeyError, TypeError) as error:
            raise ValueError('Invalid protected proposal') from error
        return action, parameters

    def _operation(self, identifier):
        action, parameters = self._proposal(identifier)
        if action not in self._policy().enabled:
            raise ValueError('Action is no longer enabled')
        return confirmation(action, parameters, installed_commit=None, artifact_sha256=None,
                            summary=SUMMARIES[action])

    def view(self, identifier):
        action, parameters = self._proposal(identifier)
        policy = self._policy()
        with closing(sqlite3.connect(self.store.path)) as db:
            bound = db.execute('''SELECT p.job_id,a.operation FROM proposal_jobs p
                JOIN authorized_jobs a ON a.id=p.job_id WHERE p.proposal_id=?''', (identifier,)).fetchone()
        job_id = bound[0] if bound else None
        status = self.execution.status(job_id) if job_id else None
        # Operation text is derived only from protected code and stored intent.
        operation = confirmation(action, parameters, installed_commit=None, artifact_sha256=None,
                                 summary=SUMMARIES[action])
        if bound:
            operation = bound[1]
        return {'id': identifier, 'action': action, 'operation': json.loads(operation),
                'enabled': action in policy.enabled, 'return_url': self.return_url,
                'state': status['state'] if status else 'authorized' if job_id else 'awaiting_confirmation',
                'job_id': job_id, 'result_code': status['result_code'] if status else None,
                'evidence': 'user_service_report' if action == 'restart-bridge' else 'system_service_health'}

    def challenge(self, identifier):
        result = self.store.challenge(self._operation(identifier), proposal_id=identifier)
        return {**result, 'proposal_id': identifier}

    def authorize(self, identifier, challenge_id, assertion):
        self.store.authorize(challenge_id, assertion, current_operation=self._operation(identifier), proposal_id=identifier)
        return self.view(identifier)
