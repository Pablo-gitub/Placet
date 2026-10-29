"""stream_json: what is skipped must be valid JSON too, the prefix cap counts bytes, and nesting is bounded."""
import json

import httpx
import pytest

from bridge.channels import ChannelError, ChannelUncertain
from bridge.stream_json import StreamJsonError, first_element
from tests.test_antigravity_stream_config import FIRST, SID, adapter, chunked, sent, whole

OK = json.dumps(FIRST).encode()


def read(data: bytes, chunk=None, **kw):
    chunks = list(chunked(data, chunk)) if chunk else [data]
    return first_element(chunks, "steps", **kw)


# --- 1. the prefix is validated, value by value --------------------------------------------------------------------


@pytest.mark.parametrize("prefix", [
    b'"ignored":not-json,',                 # an illegal scalar
    b'"ignored":[},',                       # mismatched delimiters
    b'"ignored":{"a":1],',
    b'"ignored":[1,2}',
    b'"ignored":"bad \\q escape",',          # an invalid escape
    b'"ignored":"bad \\u12G4 escape",',      # a short or non-hex unicode escape
    b'"ignored":"line\nbreak",',             # a raw control character in a string
    b'"ignored":"tab\there",',
    b'"ignored":01,',                        # a number with a leading zero
    b'"ignored":1.,',
    b'"ignored":.5,',
    b'"ignored":+1,',
    b'"ignored":1e,',
    b'"ignored":TRUE,',
    b'"ignored":nul,',
    b'"ignored":,',                          # no value
    b'"ignored" 1,',                         # no colon
    b'ignored:1,',                           # a key that is not a string
    b'"a":1,,',                              # an empty member
    b'"a":[1,],',                            # a trailing comma
    b'"a":{"b":1,},',
    b'"a":[1 2],',
    b'"a":{"b" "c"},',
    b"'a':1,",                               # single quotes
])
@pytest.mark.parametrize("chunk", [None, 1, 5])
def test_an_invalid_prefix_is_never_accepted(prefix, chunk):
    with pytest.raises(StreamJsonError) as error:
        read(whole(prefix=prefix, suffix=b"]}"), chunk)
    assert error.value.kind == "malformed"


@pytest.mark.parametrize("prefix", [
    b'"a":null,"b":true,"c":false,',
    b'"n":[0,-0,1,-1.5,2e10,2E+3,1.5e-7,12345678901234567890],',
    '"s":"\\" \\\\ \\/ \\b \\f \\n \\r \\t \\u00e9 \\ud83d\\ude00 é 日本",'.encode(),
    b'"o":{"a":{"b":[{"c":[]}]}},"e":{},"l":[],',
    b'\n  "ws" :\t[ 1 ,\r\n 2 ]  ,\n',
])
@pytest.mark.parametrize("chunk", [None, 1, 3, 17])
def test_valid_but_odd_prefixes_are_fine(prefix, chunk):
    assert read(whole(prefix=prefix, suffix=b"]}"), chunk) == FIRST


@pytest.mark.parametrize("data", [
    b'{"steps":[{"a":not-json}]}',           # the element itself is held to the grammar too
    b'{"steps":[{"a":[},]}]}',
])
def test_the_element_is_validated_before_it_is_returned(data):
    with pytest.raises(StreamJsonError) as error:
        read(data)
    assert error.value.kind == "malformed"


def test_what_is_never_read_is_never_judged():
    # The first element is complete: a broken tail is not read (the stream is closed by then).
    data = whole(suffix=b',{"broken": nonsense ]}}}')
    assert read(data, 7) == FIRST


@pytest.mark.parametrize("prefix", [b'"ignored":not-json,', b'"ignored":[},', b'"x":"\\q",', b'"x":"a\nb",'])
def test_no_send_happens_after_an_invalid_prefix(prefix):
    channel, calls, _ = adapter([whole(prefix=prefix, suffix=b"]}")])
    with pytest.raises(ChannelError) as error:
        channel.send(SID, "ciao")
    assert (error.value.code, error.value.phase) == ("user_config_unreadable", "pre_dispatch")
    assert not sent(calls)


# --- 2. the prefix cap counts bytes ---------------------------------------------------------------------------------


def exact_prefix(n_bytes: int, filler: str = "a") -> bytes:
    """A document whose element starts after exactly n_bytes."""
    head = b'{"padding":"'
    tail = b'","steps":['
    room = n_bytes - len(head) - len(tail)
    unit = len(filler.encode())
    assert room >= 0 and room % unit == 0
    return head + filler.encode() * (room // unit) + tail + b"{}]}"


def test_the_repro_from_the_review_is_refused():
    with pytest.raises(StreamJsonError) as error:
        first_element([b'{"padding":"' + b"a" * 110 + b'","steps":[{}]}'], "steps", max_prefix=100)
    assert error.value.kind == "too_large"


@pytest.mark.parametrize("chunk", [None, 1, 7, 16, 33])
@pytest.mark.parametrize("filler", ["a", "é", "日", "😀"])
def test_the_prefix_cap_holds_at_n_and_n_plus_one_bytes(chunk, filler):
    n = 120
    unit = len(filler.encode())
    # the largest prefix <= n that this filler can make, and one more filler beyond n
    inside = n - ((n - len(b'{"padding":"') - len(b'","steps":[')) % unit)
    inside_doc = exact_prefix(inside, filler)
    assert read(inside_doc, chunk, max_prefix=n) == {}
    outside_doc = exact_prefix(inside + unit * ((n + 1 - inside) // unit + 1), filler)
    with pytest.raises(StreamJsonError) as error:
        read(outside_doc, chunk, max_prefix=n)
    assert error.value.kind == "too_large"


@pytest.mark.parametrize("chunk", [None, 1, 16])
def test_the_prefix_cap_is_exact_at_the_boundary_in_ascii(chunk):
    n = 100
    assert read(exact_prefix(n), chunk, max_prefix=n) == {}                 # exactly N bytes before the element
    with pytest.raises(StreamJsonError) as error:
        read(exact_prefix(n + 1), chunk, max_prefix=n)                      # N + 1
    assert error.value.kind == "too_large"


def test_seventy_emoji_in_small_chunks_do_not_get_past_a_100_byte_prefix():
    data = b'{"padding":"' + "😀".encode() * 70 + b'","steps":[{}]}'
    with pytest.raises(StreamJsonError) as error:
        read(data, 16, max_prefix=100)
    assert error.value.kind == "too_large"


def test_the_channel_asks_for_explicit_chunks_so_the_overshoot_is_bounded():
    seen = []
    real = httpx.Response.iter_bytes

    def spy(self, chunk_size=None):
        seen.append(chunk_size)
        return real(self, chunk_size)

    httpx.Response.iter_bytes = spy
    try:
        channel, calls, _ = adapter([whole(suffix=b"]}")])
        channel.send(SID, "ciao")
    finally:
        httpx.Response.iter_bytes = real
    explicit = [size for size in seen if size]                     # the other calls are httpx's own reads of small answers
    assert explicit and all(1024 <= size <= 64 * 1024 for size in explicit)


def test_the_element_limit_stays_separate_from_the_prefix_limit():
    big = b'{"steps":[{"a":"' + b"x" * 300 + b'"}]}'
    assert read(big, 16, max_prefix=20, max_element=1000)["a"].startswith("x")       # a small prefix, a larger element
    with pytest.raises(StreamJsonError) as error:
        read(big, 16, max_prefix=1000, max_element=100)
    assert error.value.kind == "too_large"


# --- 3. deep nesting -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("where", ["element", "prefix"])
def test_a_deeply_nested_value_is_refused_by_name_not_by_a_recursion_error(where):
    deep = b"[" * 10000 + b"]" * 10000
    data = (b'{"steps":[{"a":' + deep + b"}]}") if where == "element" else (b'{"x":' + deep + b',"steps":[{}]}')
    with pytest.raises(StreamJsonError) as error:
        read(data, 4096)
    assert error.value.kind in ("malformed", "too_deep")


def test_reasonable_nesting_still_passes():
    deep = b"[" * 100 + b"]" * 100
    assert read(b'{"steps":[{"a":' + deep + b"}]}")["a"]


@pytest.mark.parametrize("where", ["element", "prefix"])
def test_no_send_and_no_uncertain_outcome_after_deep_nesting(where):
    deep = b"[" * 10000 + b"]" * 10000
    data = (b'{"steps":[{"type":"CORTEX_STEP_TYPE_USER_INPUT","z":' + deep + b"}]}") if where == "element" \
        else (b'{"x":' + deep + b',"steps":[' + OK + b"]}")
    channel, calls, _ = adapter([data])
    with pytest.raises(ChannelError) as error:
        channel.send(SID, "ciao")
    assert not isinstance(error.value, ChannelUncertain)
    assert (error.value.code, error.value.phase) == ("user_config_unreadable", "pre_dispatch")
    assert not sent(calls)


def test_a_recursion_error_from_the_json_library_is_translated():
    # Belt and braces: whatever json.loads raises on a document that got past the scan is a malformed one.
    import bridge.stream_json as module
    original = module.json.loads

    def boom(text, *a, **k):
        if text.startswith('{"a"'):               # the element; the key "steps" is parsed by the same function
            raise RecursionError("too deep")
        return original(text, *a, **k)

    module.json.loads = boom
    try:
        with pytest.raises(StreamJsonError) as error:
            read(b'{"steps":[{"a":1}]}')
    finally:
        module.json.loads = original
    assert error.value.kind == "malformed"
