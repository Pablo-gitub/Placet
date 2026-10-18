"""Private desktop transport, protocol isolation, and suspended-mail recovery."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from bridge.desktop_proxy import DesktopProxy, private_directory
from bridge.bridge_daemon import BridgeDaemon
from bridge.channels import ChannelError, CodexChannel
from bridge.stores import SessionMail
from tests.test_session_messages import setup, start, write  # noqa: F401
from tests.helpers import agent_client

SID = '01a1467c-2982-78a0-8246-61283d1c3e80'


class Input:
    def __init__(self):
        self.messages = []

    def write(self, raw):
        self.messages.append(json.loads(raw))

    async def drain(self):
        pass


@pytest.mark.asyncio
async def test_desktop_handshake_and_server_replies_are_isolated():
    source, replies, output = asyncio.StreamReader(), asyncio.StreamReader(), []
    stdin = Input()
    proxy = DesktopProxy(SimpleNamespace(stdin=stdin, stdout=replies), output.append)
    with pytest.raises(ConnectionError):
        await proxy.rpc('thread/read', {})
    raw = b'{"method":"initialize","id":1,"params":{}}\n{"method":"initialized"}\n'
    source.feed_data(raw)
    source.feed_eof()
    await proxy.desktop_input(source)
    assert stdin.messages == [json.loads(x) for x in raw.splitlines()]
    task = asyncio.create_task(proxy.server_output())
    call = asyncio.create_task(proxy.status(SID))
    await asyncio.sleep(0)
    own_id = stdin.messages[-1]['id']
    replies.feed_data((json.dumps({'id': own_id, 'result': {'thread': {'status': {'type': 'idle'}}}}) + '\n').encode())
    replies.feed_data(b'{"method":"turn/completed","params":{}}\n')
    replies.feed_data(b'{"id":1,"result":{}}\n')
    replies.feed_data(b'{"id":12,"method":"item/commandExecution/requestApproval","params":{}}\n')
    assert await call == {'state': 'idle'}
    # A late answer from an expired adapter call is also hidden from desktop.
    replies.feed_data((json.dumps({'id': proxy.prefix + 'expired', 'result': {}}) + '\n').encode())
    replies.feed_eof()
    await task
    assert len(output) == 3
    assert json.loads(output[2])['method'].endswith('requestApproval')


@pytest.mark.asyncio
async def test_resume_only_loads_unloaded_threads_and_rejects_other_operations():
    proxy = DesktopProxy(None, None)
    calls = []
    states = iter(['notLoaded', 'idle', 'active'])

    async def rpc(method, params):
        calls.append((method, params))
        if method == 'thread/read':
            return {'thread': {'status': {'type': next(states)}}}
        return {}

    proxy.rpc = rpc
    assert await proxy.operation({'operation': 'resume', 'thread_id': SID}) == {'state': 'idle'}
    assert await proxy.operation({'operation': 'resume', 'thread_id': SID}) == {'state': 'active'}
    assert [m for m, _ in calls].count('thread/resume') == 1
    for bad in [{'operation': 'turn/start', 'thread_id': SID},
                {'operation': 'resume', 'thread_id': '../x'},
                {'operation': 'resume', 'thread_id': SID, 'params': {'model': 'x'}}]:
        with pytest.raises(ValueError):
            await proxy.operation(bad)


def test_private_socket_directory_refuses_shared_or_symlinked_path(tmp_path):
    private = tmp_path / 'private'
    private_directory(private)
    private.chmod(0o755)
    with pytest.raises(PermissionError):
        private_directory(private)
    link = tmp_path / 'link'
    link.symlink_to(private)
    with pytest.raises(PermissionError):
        private_directory(link)


def test_bridge_never_claims_without_runtime_confirmation():
    store = MagicMock()
    channel = CodexChannel(desktop=MagicMock())
    channel.send = MagicMock()
    daemon = BridgeDaemon(store=store, tmux=MagicMock(), channels={'codex': channel})
    target = [{'id': SID, 'provider': 'codex', 'suspended': True}]
    for result in [False, OSError('down')]:
        if isinstance(result, Exception):
            channel.desktop.prepare.side_effect = result
        else:
            channel.desktop.prepare.return_value = result
        assert daemon.process_session_mail(target) == 0
    store.take_session_mail.assert_not_called()
    channel.desktop.prepare.side_effect = None
    channel.desktop.prepare.return_value = True
    store.runtime_ready.return_value = True
    store.take_session_mail.return_value = SessionMail('injection', 38, 'test')
    assert daemon.process_session_mail(target) == 1
    channel.send.assert_called_once_with(SID, 'test')
    store.confirm_injection.assert_called_once_with(38)
    # Native-link setup queues first, confirms, then opens without a proxy.
    native = CodexChannel()
    native.send = MagicMock()
    native.open_conversation = MagicMock()
    daemon.channels = {'codex': native}
    assert daemon.process_session_mail(target) == 1
    native.open_conversation.assert_called_once_with(SID)


@pytest.mark.asyncio
async def test_suspended_mail_recovery_does_not_invent_execution(setup):  # noqa: F811
    app, db = setup
    await start(app, 'codex', SID, 'codex')
    db.suspend_session(SID)
    req_id = (await write(app, SID, 'test')).json()['id']
    async with agent_client(app, 'tok-bridge') as bridge:
        data = (await bridge.get('/api/bridge/work')).json()
        assert data['sessions'] == []
        assert data['wake_sessions'] == [{'id': SID, 'provider': 'codex', 'suspended': True}]
        result = await bridge.post(f'/api/bridge/sessions/{SID}/runtime-ready')
        assert result.status_code == 200
        assert db.get_session(SID).suspended_at is None
        assert db.get_request(req_id).execution == 'unobserved'
        taken = await bridge.post(f'/api/bridge/sessions/{SID}/take')
        assert taken.json()['request_id'] == req_id
        assert (await bridge.post(f'/api/bridge/sessions/{SID}/take')).status_code == 204
    async with agent_client(app, 'tok-codex') as codex:
        assert (await codex.post(f'/api/bridge/sessions/{SID}/runtime-ready')).status_code == 403
    db.end_session(SID)
    assert not db.runtime_ready(SID)


def test_real_stdio_socket_roundtrip_with_fake_engine(tmp_path):
    import os
    from pathlib import Path
    import select
    import subprocess
    import sys
    import time
    from bridge.desktop_client import DesktopClient

    fake = tmp_path / 'fake-engine'
    fake.write_text('#!' + sys.executable + '\n' + '''import json,sys
loaded=False
for raw in sys.stdin:
 m=json.loads(raw)
 if 'id' not in m: continue
 method=m['method']
 if method=='initialize': result={'userAgent':'test'}
 elif method=='thread/read': result={'thread':{'status':{'type':'idle' if loaded else 'notLoaded'}}}
 elif method=='thread/resume':
  loaded=True
  result={}
  print(json.dumps({'method':'thread/started','params':{}}),flush=True)
 else: raise AssertionError(method)
 print(json.dumps({'id':m['id'],'result':result}),flush=True)
''')
    fake.chmod(0o755)
    folder = tmp_path / 'adapter'
    sock = folder / 'codex.sock'
    env = {**os.environ, 'PLACET_CODEX_REAL_BIN': str(fake), 'PLACET_DESKTOP_SOCKET': str(sock)}
    child = subprocess.Popen([sys.executable, '-m', 'bridge.desktop_proxy', 'app-server'],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             env=env, cwd=str(Path(__file__).resolve().parents[1]))
    try:
        child.stdin.write(b'{"id":1,"method":"initialize","params":{}}\n{"method":"initialized"}\n')
        child.stdin.flush()
        assert select.select([child.stdout], [], [], 5)[0]
        assert json.loads(child.stdout.readline())['id'] == 1
        for _ in range(100):
            if sock.exists(): break
            time.sleep(0.01)
        assert sock.stat().st_mode & 0o777 == 0o600
        assert DesktopClient(sock).prepare(SID)
        assert select.select([child.stdout], [], [], 5)[0]
        assert json.loads(child.stdout.readline())['method'] == 'thread/started'
        child.stdin.close()
        assert child.wait(timeout=5) == 0, child.stderr.read().decode()
        assert not sock.exists()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_adapter_does_not_retry_an_uncertain_queue_delivery():
    import subprocess
    from bridge.channels import ChannelUncertain
    store = MagicMock()
    store.take_session_mail.return_value = SessionMail('injection', 7, 'test')
    channel = CodexChannel(desktop=MagicMock())
    channel.desktop.prepare.return_value = True
    channel.send = MagicMock(side_effect=ChannelUncertain('queue timeout'))
    daemon = BridgeDaemon(store=store, tmux=MagicMock(), channels={'codex': channel})
    assert daemon.process_session_mail([{'id': SID, 'provider': 'codex'}]) == 0
    store.mark_uncertain.assert_called_once_with(7)
    store.release_injection.assert_not_called()
    store.confirm_injection.assert_not_called()


def test_allowlist_limits_wakeup_without_blocking_existing_active_sessions(tmp_path):
    from bridge.desktop_client import DesktopClient
    adapter = DesktopClient(tmp_path / 'missing.sock', allowed_threads=[])
    assert not adapter.enabled(SID)
    assert not adapter.prepare(SID)
    channel = CodexChannel(desktop=adapter)
    channel.send = MagicMock()
    store = MagicMock()
    store.take_session_mail.return_value = SessionMail('injection', 8, 'test')
    daemon = BridgeDaemon(store=store, tmux=MagicMock(), channels={'codex': channel})
    assert daemon.process_session_mail([{'id': SID, 'provider': 'codex', 'suspended': True}]) == 0
    store.take_session_mail.assert_not_called()
    assert daemon.process_session_mail([{'id': SID, 'provider': 'codex'}]) == 1
    channel.send.assert_called_once()

@pytest.mark.asyncio
async def test_initialize_response_without_initialized_notification():
    source, replies, output = asyncio.StreamReader(), asyncio.StreamReader(), []
    proxy = DesktopProxy(SimpleNamespace(stdin=Input(), stdout=replies), output.append)
    source.feed_data(b'{"method":"initialize","id":"desktop-init","params":{}}\n')
    source.feed_eof()
    await proxy.desktop_input(source)
    assert not proxy.ready.is_set()
    replies.feed_data(b'{"id":"unrelated","result":{}}\n')
    replies.feed_data(b'{"id":"desktop-init","error":{"code":-1}}\n')
    task = asyncio.create_task(proxy.server_output())
    await asyncio.sleep(0)
    assert not proxy.ready.is_set()
    replies.feed_data(b'{"id":"desktop-init","result":{}}\n')
    await asyncio.wait_for(proxy.ready.wait(), 1)
    replies.feed_eof()
    await task
    assert len(output) == 3

@pytest.mark.asyncio
async def test_native_wakeup_keeps_suspension_and_one_in_flight(setup):
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
