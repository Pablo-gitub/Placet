"""Human-only reserved-action intents, with no administrative signing authority.

This ordinary backend module has no dependency on the protected administrative
package or its crypto environment. It can only use three public Unix methods.
The separate root broker validates the backend UID and requires a human passkey
on its own origin before creating an executable authorization.
"""
import json
import socket
import struct
from typing import Literal
from urllib.parse import urlsplit, parse_qs

from fastapi import Depends, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, StrictBool
from starlette.concurrency import run_in_threadpool

from .auth import Principal, require_human


SOCKET = '/run/placet-admin-control/backend.sock'
LIMIT = 100000
METHODS = {'catalogue': set(), 'propose': {'request_key', 'action', 'parameters'}, 'view': {'identifier'}}
Action = Literal['restart-backend', 'restart-bridge']
State = Literal['awaiting_confirmation', 'expired', 'authorized', 'running', 'succeeded', 'failed', 'outcome_unknown']


class AdminUnavailable(Exception):
    """Missing/unreachable service or invalid public response, with no secrets."""


class AdminRejected(Exception):
    """Protected policy rejected the proposal, not an execution failure."""


class AdminCommands:
    def __init__(self, path=SOCKET):
        self.path = str(path)

    def call(self, method, **params):
        if method not in METHODS or set(params) != METHODS[method]:
            raise ValueError('Only administrative proposals and public reads are allowed')
        raw = json.dumps({'method': method, 'params': params}).encode() + b'\n'
        if len(raw) > LIMIT:
            raise ValueError('Administrative request exceeds limit')
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(3)
                connection.connect(self.path)
                uid = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[1]
                if uid != 0:
                    raise AdminUnavailable()
                connection.sendall(raw)
                with connection.makefile('rb') as stream:
                    response = stream.readline(LIMIT + 1)
            if len(response) > LIMIT or not response.endswith(b'\n'):
                raise AdminUnavailable()
            envelope = json.loads(response)
            if envelope.get('ok') is not True:
                raise AdminRejected()
            return envelope['result']
        except (OSError, KeyError, TypeError, AttributeError, json.JSONDecodeError):
            raise AdminUnavailable() from None


class ProposalInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_key: str = Field(pattern=r'^[A-Za-z0-9_-]{1,128}$')
    action: Action
    parameters: dict = Field(default_factory=dict, max_length=0)


class PublicAction(BaseModel):
    id: Action
    summary: str = Field(max_length=512)
    parameters: dict = Field(max_length=0)
    enabled: StrictBool


class PublicProposal(BaseModel):
    # Ignore all non-public broker fields, including future credential metadata.
    id: str = Field(pattern=r'^[a-f0-9]{32}$')
    action: Action
    enabled: StrictBool
    state: State
    expires_at: FiniteFloat = Field(gt=0)
    confirmation_url: str = Field(pattern=r'^https://[a-z0-9.-]+/\?proposal=[a-f0-9]{32}$', max_length=512)
    job_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')
    result_code: str | None = Field(default=None, pattern=r'^[a-z_]{1,64}$')
    evidence: Literal['user_service_report', 'system_service_health']


def public_proposal(raw):
    result = PublicProposal.model_validate(raw).model_dump(mode='json')
    if parse_qs(urlsplit(result['confirmation_url']).query) != {'proposal': [result['id']]}:
        raise AdminUnavailable()
    if (result['state'] in ('awaiting_confirmation', 'expired')) != (result['job_id'] is None):
        raise AdminUnavailable()
    return result


def public_catalogue(raw):
    if (not isinstance(raw, dict) or type(raw.get('revision')) is not int or raw['revision'] != 1
            or type(raw.get('execution_enabled')) is not bool or not isinstance(raw.get('actions'), list)
            or len(raw['actions']) > 2 or not isinstance(raw.get('recent', []), list) or len(raw.get('recent', [])) > 10):
        raise AdminUnavailable()
    actions = [PublicAction.model_validate(action).model_dump() for action in raw['actions']]
    if len({action['id'] for action in actions}) != len(actions) or raw['execution_enabled'] != any(action['enabled'] for action in actions):
        raise AdminUnavailable()
    return {'status': 'available', 'revision': 1, 'execution_enabled': raw['execution_enabled'],
            'actions': [action for action in actions if action['enabled']],
            'recent': [public_proposal(item) for item in raw.get('recent', [])]}


def failure(code, status):
    detail = ('Administrative service unavailable. Recheck status or retry the same request key; do not create another proposal.'
              if code == 'admin.unavailable' else 'The administrative proposal is unavailable or rejected by policy.')
    return JSONResponse({'code': code, 'params': {}, 'detail': detail}, status_code=status,
                        headers={'Cache-Control': 'no-store'})


def install_routes(app, principal, client):
    @app.get('/api/admin/catalogue')
    async def catalogue(response: Response, who: Principal = Depends(principal)):
        require_human(who)
        response.headers['Cache-Control'] = 'no-store'
        try:
            return public_catalogue(await run_in_threadpool(client.call, 'catalogue'))
        except (AdminUnavailable, AdminRejected, ValueError, KeyError, TypeError):
            return {'status': 'not_configured', 'execution_enabled': False, 'actions': []}

    @app.post('/api/admin/proposals', status_code=201)
    async def propose(body: ProposalInput, response: Response, who: Principal = Depends(principal)):
        require_human(who)
        response.headers['Cache-Control'] = 'no-store'
        try:
            return public_proposal(await run_in_threadpool(client.call, 'propose', **body.model_dump()))
        except AdminRejected:
            return failure('admin.rejected', 409)
        except (AdminUnavailable, ValueError, KeyError, TypeError):
            return failure('admin.unavailable', 503)

    @app.get('/api/admin/proposals/{identifier}')
    async def proposal(identifier: str, response: Response, who: Principal = Depends(principal)):
        require_human(who)
        response.headers['Cache-Control'] = 'no-store'
        if len(identifier) != 32 or any(c not in 'abcdef0123456789' for c in identifier):
            return failure('admin.rejected', 404)
        try:
            return public_proposal(await run_in_threadpool(client.call, 'view', identifier=identifier))
        except AdminRejected:
            return failure('admin.rejected', 409)
        except (AdminUnavailable, ValueError, KeyError, TypeError):
            return failure('admin.unavailable', 503)
