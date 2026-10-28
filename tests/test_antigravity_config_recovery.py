"""Recover a current user configuration when the ordinary trajectory is stale."""
import json

import httpx
import pytest

from bridge.channels import AntigravityChannel, ChannelError


SID = 'conversation'
CONFIG = {'plannerConfig': {'requestedModel': {'model': 'current'}}}


def adapter(summary=None, steps=None, status=200):
    calls = []
    summary = summary if summary is not None else {'stepCount': 27395, 'lastUserInputStepIndex': 27382, 'lastUserInputTime': 'latest'}
    steps = steps if steps is not None else [
        {'type': 'CORTEX_STEP_TYPE_USER_INPUT', 'metadata': {'createdAt': 'latest'}, 'userInput': {'userConfig': CONFIG}},
        {'type': 'CORTEX_STEP_TYPE_PLANNER_RESPONSE'},
    ]

    def handle(request):
        method = request.url.path.rsplit('/', 1)[-1]
        body = json.loads(request.content)
        calls.append((method, body))
        if method == 'GetCascadeTrajectory':
            return httpx.Response(200, json={'trajectory': {'cascadeId': SID, 'steps': [
                {'type': 'CORTEX_STEP_TYPE_USER_INPUT'}]}})
        if method == 'GetAllCascadeTrajectories':
            return httpx.Response(200, json={'trajectorySummaries': {SID: summary, 'other': {
                'stepCount': 1, 'lastUserInputStepIndex': 0}}})
        if method == 'GetCascadeTrajectorySteps':
            return httpx.Response(status, json={'steps': steps})
        assert method == 'SendUserCascadeMessage'
        return httpx.Response(200, json={})

    channel = AntigravityChannel(client=httpx.Client(transport=httpx.MockTransport(handle)),
                                 discover=lambda: [(1, 'token', 'test')], ports_of=lambda _: [1234])
    return channel, calls


def test_stale_trajectory_recovers_latest_user_config_by_exact_offset():
    channel, calls = adapter()
    channel.send(SID, 'hello')
    assert calls[-2] == ('GetCascadeTrajectorySteps', {
        'cascadeId': SID, 'stepOffset': 27382, 'verbosity': 1})
    assert calls[-1][1]['cascadeConfig'] == CONFIG
    assert calls[-1][1]['deliveryStrategy'] == 'MESSAGE_DELIVERY_STRATEGY_WHEN_IDLE'


@pytest.mark.parametrize('summary', [
    {}, {'stepCount': 10}, {'stepCount': 10, 'lastUserInputStepIndex': -1},
    {'stepCount': 10, 'lastUserInputStepIndex': 10},
    {'stepCount': 10, 'lastUserInputStepIndex': True},
    {'stepCount': 10, 'lastUserInputStepIndex': '3'},
])
def test_invalid_summary_never_fetches_history_or_sends(summary):
    channel, calls = adapter(summary=summary)
    with pytest.raises(ChannelError) as error:
        channel.send(SID, 'hello')
    assert error.value.code == 'missing_user_config'
    assert all(method not in ('GetCascadeTrajectorySteps', 'SendUserCascadeMessage') for method, _ in calls)


@pytest.mark.parametrize('steps', [[], [
    {'type': 'CORTEX_STEP_TYPE_PLANNER_RESPONSE'},
    {'type': 'CORTEX_STEP_TYPE_USER_INPUT', 'userInput': {'userConfig': CONFIG}},
], [{'type': 'CORTEX_STEP_TYPE_USER_INPUT', 'userInput': {}}]])
def test_missing_or_misaligned_latest_user_never_sends(steps):
    channel, calls = adapter(steps=steps)
    with pytest.raises(ChannelError):
        channel.send(SID, 'hello')
    assert calls[-1][0] != 'SendUserCascadeMessage'


def test_unavailable_recovery_remains_pre_dispatch():
    channel, calls = adapter(status=503)
    with pytest.raises(ChannelError) as error:
        channel.send(SID, 'hello')
    assert error.value.phase == 'pre_dispatch'
    assert calls[-1][0] != 'SendUserCascadeMessage'


def test_ignored_offset_with_old_user_timestamp_is_rejected():
    channel, calls = adapter(
        summary={'stepCount': 100, 'lastUserInputStepIndex': 90, 'lastUserInputTime': 'new'},
        steps=[{'type': 'CORTEX_STEP_TYPE_USER_INPUT', 'metadata': {'createdAt': 'old'},
                'userInput': {'userConfig': CONFIG}}])
    with pytest.raises(ChannelError):
        channel.send(SID, 'hello')
    assert calls[-1][0] != 'SendUserCascadeMessage'


def test_recovery_is_bounded_and_does_not_send_oversize_history():
    channel, calls = adapter(steps=[{'type': 'CORTEX_STEP_TYPE_USER_INPUT',
                                   'userInput': {'userConfig': CONFIG, 'text': 'x' * (8 * 1024 * 1024)}}])
    with pytest.raises(ChannelError):
        channel.send(SID, 'hello')
    assert calls[-1][0] != 'SendUserCascadeMessage'


def test_existing_configuration_does_not_trigger_extra_reads():
    channel, calls = adapter()
    channel._trajectory = lambda _: ('http://127.0.0.1:1234', 'token', {
        'steps': [{'userInput': {'userConfig': CONFIG}}]})
    channel.send(SID, 'hello', now=True)
    assert [method for method, _ in calls] == ['SendUserCascadeMessage']
    assert calls[0][1]['deliveryStrategy'] == 'MESSAGE_DELIVERY_STRATEGY_NEXT_INVOCATION'


def test_missing_timestamp_cannot_validate_a_server_ignoring_the_offset():
    channel, calls = adapter(summary={'stepCount': 100, 'lastUserInputStepIndex': 90})
    with pytest.raises(ChannelError):
        channel.send(SID, 'hello')
    assert calls[-1][0] != 'SendUserCascadeMessage'
