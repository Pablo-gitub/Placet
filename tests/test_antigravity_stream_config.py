"""Only the first step of GetCascadeTrajectorySteps is read, as a stream; the rest of a huge answer is never downloaded."""
import json

import httpx
import pytest

from bridge.channels import DETERMINISTIC_CODES, AntigravityChannel, ChannelError

SID = "conversation"
CONFIG = {"plannerConfig": {"requestedModel": {"model": "naïve 日本語 😀"}}, "note": 'say "hi" \\ {] }'}
STAMP = "2026-10-29T10:00:00Z"
FIRST = {"type": "CORTEX_STEP_TYPE_USER_INPUT", "metadata": {"createdAt": STAMP},
         "userInput": {"items": [{"text": 'una "frase" con } e ] e \\ e steps'}], "userConfig": CONFIG, "lastUserConfig": CONFIG}}
SUMMARY = {"stepCount": 27562, "lastUserInputStepIndex": 27425, "lastUserInputTime": STAMP}
MIB = 1024 * 1024


class Counting(httpx.SyncByteStream):
    """A response body that says how much of it was read, and whether it was closed."""

    def __init__(self, chunks):
        self.chunks, self.read, self.closed = chunks, 0, False

    def __iter__(self):
        for chunk in self.chunks:
            self.read += len(chunk)
            yield chunk

    def close(self):
        self.closed = True


def chunked(data: bytes, size: int):
    return (data[i:i + size] for i in range(0, len(data), size))


def tail(megabytes):
    """Further steps, produced lazily, as many megabytes as asked."""
    filler = json.dumps({"type": "CORTEX_STEP_TYPE_TOOL", "x": "y" * 4000}).encode()
    for _ in range(megabytes * MIB // len(filler) + 1):
        yield b"," + filler


def adapter(body, summary=None, status=200):
    calls, bodies = [], []

    def handle(request):
        method = request.url.path.rsplit("/", 1)[-1]
        calls.append((method, json.loads(request.content)))
        if method == "GetCascadeTrajectory":
            return httpx.Response(200, json={"trajectory": {"cascadeId": SID, "steps": [{"type": "CORTEX_STEP_TYPE_USER_INPUT"}]}})
        if method == "GetAllCascadeTrajectories":
            return httpx.Response(200, json={"trajectorySummaries": {SID: summary or SUMMARY}})
        if method == "GetCascadeTrajectorySteps":
            stream = body() if callable(body) else Counting(body)
            bodies.append(stream)
            return httpx.Response(status, stream=stream)
        return httpx.Response(200, json={})

    channel = AntigravityChannel(client=httpx.Client(transport=httpx.MockTransport(handle)),
                                 discover=lambda: [(1, "token", "test")], ports_of=lambda _: [1234])
    return channel, calls, bodies


def whole(first=FIRST, prefix=b"", suffix=b"") -> bytes:
    return b'{' + prefix + b'"steps":[' + json.dumps(first, ensure_ascii=False).encode() + suffix


def sent(calls):
    return [c for c in calls if c[0] == "SendUserCascadeMessage"]


def test_a_small_first_step_followed_by_a_huge_tail_is_enough_and_the_tail_is_never_read():
    def stream():
        head = whole()
        return Counting(iter([head[i:i + 4096] for i in range(0, len(head), 4096)] + list(tail(12))))
    channel, calls, bodies = adapter(stream)
    channel.send(SID, "ciao")
    assert sent(calls)[0][1]["cascadeConfig"] == CONFIG
    assert bodies[0].read < 128 * 1024                      # a few KiB of the first step, not 12 MiB
    assert bodies[0].closed is True                          # the stream was closed as soon as the step was complete
    assert [m for m, _ in calls][-2:] == ["GetCascadeTrajectorySteps", "SendUserCascadeMessage"]


@pytest.mark.parametrize("size", [1, 2, 3, 5, 7, 64, 1000])
def test_the_same_answer_cut_anywhere_gives_the_same_configuration(size):
    data = whole(suffix=b"," + json.dumps({"type": "CORTEX_STEP_TYPE_TOOL"}).encode() + b"]}")
    channel, calls, _ = adapter(list(chunked(data, size)))
    channel.send(SID, "ciao")
    assert sent(calls)[0][1]["cascadeConfig"] == CONFIG


def test_fields_before_steps_are_skipped_even_when_they_look_like_it():
    prefix = (b'"meta":{"steps":[1,2,{"steps":"no"}],"x":"}]\\"steps\\":["},"list":[{"a":[]},"steps"],"n":-1.5e3,"ok":true,'
              b'"nothing":null,')
    channel, calls, _ = adapter(list(chunked(whole(prefix=prefix, suffix=b"]}"), 3)))
    channel.send(SID, "ciao")
    assert sent(calls)[0][1]["cascadeConfig"] == CONFIG


def test_whitespace_and_newlines_around_the_array_are_fine():
    data = b'{\n  "steps" :\n [\n  ' + json.dumps(FIRST).encode() + b'\n ] }'
    channel, calls, _ = adapter(list(chunked(data, 4)))
    channel.send(SID, "ciao")
    assert sent(calls)[0][1]["cascadeConfig"] == CONFIG


def test_a_first_step_beyond_the_cap_fails_early_and_by_name():
    huge = {**FIRST, "userInput": {**FIRST["userInput"], "media": "x" * (3 * MIB)}}

    def stream():
        return Counting(chunked(whole(huge, suffix=b"]}"), 8192))
    channel, calls, bodies = adapter(stream)
    with pytest.raises(ChannelError) as error:
        channel.send(SID, "ciao")
    assert (error.value.code, error.value.phase) == ("user_config_unreadable", "pre_dispatch")
    assert bodies[0].read < 2 * MIB + 64 * 1024 and bodies[0].closed
    assert not sent(calls)


@pytest.mark.parametrize("data", [
    whole()[:-40],                                           # interrupted inside the first step
    b'{"steps":[{"type":"CORTEX_STEP_TYPE_USER_INPUT",,]}',  # malformed
    b'not json at all',
    b'{"steps":[{"a":"\xff\xfe"}]}',                         # not UTF-8
    b'{"steps":[',                                           # cut right at the start
])
def test_an_unreadable_or_interrupted_answer_fails_by_name_before_any_delivery(data):
    channel, calls, _ = adapter([data])
    with pytest.raises(ChannelError) as error:
        channel.send(SID, "ciao")
    assert (error.value.code, error.value.phase) == ("user_config_unreadable", "pre_dispatch")
    assert not sent(calls)


@pytest.mark.parametrize("data", [
    b'{"steps":[]}', b'{}', b'{"steps":null}', b'{"steps":{"a":1}}',
    json.dumps({"steps": [{"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE", "metadata": {"createdAt": STAMP}}]}).encode(),
    json.dumps({"steps": [{**FIRST, "metadata": {"createdAt": "another"}}]}).encode(),         # not the user turn we want
    json.dumps({"steps": [{"type": "CORTEX_STEP_TYPE_USER_INPUT", "metadata": {"createdAt": STAMP}, "userInput": {}}]}).encode(),
])
def test_a_first_step_that_is_not_the_latest_user_turn_with_a_configuration_is_refused(data):
    channel, calls, _ = adapter([data])
    with pytest.raises(ChannelError) as error:
        channel.send(SID, "ciao")
    assert error.value.code == "missing_user_config" and error.value.phase == "pre_dispatch"
    assert not sent(calls)


def test_http_failures_and_timeouts_are_as_before():
    channel, calls, _ = adapter([b"{}"], status=503)
    with pytest.raises(ChannelError) as error:
        channel.send(SID, "ciao")
    assert error.value.code == "missing_user_config" and not sent(calls)

    class Slow(httpx.SyncByteStream):
        def __iter__(self):
            raise httpx.ReadTimeout("slow")
            yield b""

    channel, calls, _ = adapter(lambda: Slow())
    with pytest.raises(ChannelError) as error:
        channel.send(SID, "ciao")
    assert error.value.phase == "pre_dispatch" and not sent(calls)


def test_the_new_code_is_deterministic_like_the_old_one():
    assert {"missing_user_config", "user_config_unreadable"} <= DETERMINISTIC_CODES


def test_nothing_of_the_configuration_is_in_the_error():
    channel, calls, _ = adapter([whole()[:-40]])
    with pytest.raises(ChannelError) as error:
        channel.send(SID, "ciao")
    assert "naïve" not in str(error.value) and "plannerConfig" not in str(error.value)


def test_a_huge_field_before_the_steps_is_bounded_too():
    junk = b'{"padding":"' + b"x" * (3 * MIB) + b'","steps":[' + json.dumps(FIRST).encode() + b"]}"
    channel, calls, bodies = adapter(list(chunked(junk, 65536)))
    with pytest.raises(ChannelError) as error:
        channel.send(SID, "ciao")
    assert error.value.code == "user_config_unreadable" and not sent(calls)
