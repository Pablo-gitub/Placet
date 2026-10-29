"""
Reads one value out of a huge JSON answer without downloading the rest: the first element of a top-level array.

Antigravity's GetCascadeTrajectorySteps answers with every step from `stepOffset` to the end of the conversation (12 MB
for a few dozen steps in a real case), and the step we want is the first. The answer is read as a stream and the read
stops as soon as that element is complete.

Everything that is read is held to the JSON grammar, not just counted: the fields before the array are skipped by a small
validating scanner (keys are strings, `:` and `,` where they belong, legal scalars, matching delimiters, valid escapes, no
raw control characters, bounded nesting), and the element is validated the same way before `json.loads` builds it. What
comes after the element is never read, so it is never judged.

Two separate caps: the bytes that precede the element (`max_prefix`, counted in bytes, checked as soon as more data is
needed and again when the element starts) and the element itself (`max_element`).
"""
import codecs
import json
import re
from typing import Any, Iterable, Optional

_WS = " \t\r\n"
_SCALAR_END = re.compile(r'[,}\]\s]')
_STRING_RUN = re.compile(r'[^"\\\x00-\x1f]*')
_HEX4 = re.compile(r'[0-9a-fA-F]{4}')
_NUMBER = re.compile(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?')
_LITERALS = {"true", "false", "null"}
_SIMPLE_ESCAPES = '"\\/bfnrt'
MAX_DEPTH = 200                      # nesting of the values we scan; far beyond anything a step holds
MAX_SCALAR = 64                      # characters of a number or literal


class StreamJsonError(Exception):
    """`kind`: "too_large" (a cap was passed), "malformed" (not JSON, not UTF-8, too deep) or "truncated" (cut short)."""

    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


class _Reader:
    def __init__(self, chunks: Iterable[bytes], max_element: int, max_prefix: int):
        self.chunks = iter(chunks)
        self.decoder = codecs.getincrementaldecoder("utf-8")()
        self.buf = ""
        self.pos = 0
        self.bytes_read = 0                  # raw bytes taken from the stream so far
        self.max_element, self.max_prefix = max_element, max_prefix
        self.anchor = None                   # where the element starts in `buf`, once it has

    def more(self) -> None:
        """Appends the next chunk; raises "truncated" if the answer ended, "too_large" beyond a cap."""
        if self.anchor is None:
            # Everything read so far is consumed (we ask for more only at the end of the buffer) and is still before the
            # element: if that is already past the cap, no element can start within it.
            if self.bytes_read > self.max_prefix:
                raise StreamJsonError("too_large")
        elif len(self.buf) - self.anchor > self.max_element:       # characters never outnumber bytes
            raise StreamJsonError("too_large")
        try:
            chunk = next(self.chunks)
        except StopIteration:
            try:
                tail = self.decoder.decode(b"", final=True)
            except UnicodeDecodeError:
                raise StreamJsonError("malformed") from None
            if tail:
                self.buf += tail
                return
            raise StreamJsonError("truncated") from None
        self.bytes_read += len(chunk)
        try:
            self.buf += self.decoder.decode(chunk)
        except UnicodeDecodeError:
            raise StreamJsonError("malformed") from None

    def ensure(self, end: int) -> None:
        """Holds at least `end` characters."""
        while len(self.buf) < end:
            self.more()

    def peek(self) -> str:
        self.ensure(self.pos + 1)
        return self.buf[self.pos]

    def skip_ws(self) -> str:
        while True:
            c = self.peek()
            if c not in _WS:
                return c
            self.pos += 1

    def skip_string(self) -> None:
        """At an opening quote: past the closing one, every escape and character checked."""
        self.pos += 1
        while True:
            self.pos = _STRING_RUN.match(self.buf, self.pos).end()
            if self.pos >= len(self.buf):            # the run ran into the end of what we hold: there may be more of it
                self.more()
                continue
            c = self.buf[self.pos]
            if c == '"':
                self.pos += 1
                return
            if c != "\\":
                raise StreamJsonError("malformed")                   # a raw control character
            self.ensure(self.pos + 2)
            e = self.buf[self.pos + 1]
            if e == "u":
                self.ensure(self.pos + 6)
                if not _HEX4.fullmatch(self.buf, self.pos + 2, self.pos + 6):
                    raise StreamJsonError("malformed")
                self.pos += 6
            elif e in _SIMPLE_ESCAPES:
                self.pos += 2
            else:
                raise StreamJsonError("malformed")

    def read_key(self) -> str:
        """At an opening quote: the key, decoded."""
        start = self.pos
        self.skip_string()
        try:
            key = json.loads(self.buf[start:self.pos])
        except ValueError:
            raise StreamJsonError("malformed") from None
        return key if isinstance(key, str) else ""

    def skip_scalar(self) -> None:
        """A number, true, false or null, as the grammar has them."""
        start = self.pos
        while True:
            m = _SCALAR_END.search(self.buf, start)
            if m is not None:
                token = self.buf[start:m.start()]
                break
            if len(self.buf) - start > MAX_SCALAR:
                raise StreamJsonError("malformed")
            self.more()
        if not token or len(token) > MAX_SCALAR or not (token in _LITERALS or _NUMBER.fullmatch(token)):
            raise StreamJsonError("malformed")
        self.pos = m.start()

    def skip_value(self) -> None:
        """Past one JSON value of any kind, validated, without recursion."""
        stack: list = []                     # open containers: [opener, "first" | "more"]
        want = "value"                       # "value", "key" (in an object) or "after" (a value is complete)
        while True:
            if want == "value":
                c = self.skip_ws()
                if c == "]" and stack and stack[-1] == ["[", "first"]:
                    self.pos += 1
                    stack.pop()
                    want = "after"
                elif c in "{[":
                    if len(stack) >= MAX_DEPTH:
                        raise StreamJsonError("malformed")           # nesting beyond anything a step holds
                    self.pos += 1
                    stack.append([c, "first"])
                    want = "key" if c == "{" else "value"
                    continue
                else:
                    if c == '"':
                        self.skip_string()
                    else:
                        self.skip_scalar()
                    want = "after"
                if not stack:
                    return
            elif want == "key":
                c = self.skip_ws()
                top = stack[-1]
                if c == "}" and top[1] == "first":
                    self.pos += 1
                    stack.pop()
                    want = "after"
                    if not stack:
                        return
                    continue
                if c != '"':
                    raise StreamJsonError("malformed")
                self.skip_string()
                if self.skip_ws() != ":":
                    raise StreamJsonError("malformed")
                self.pos += 1
                want = "value"
            else:
                c = self.skip_ws()
                top = stack[-1]
                if c == ",":
                    self.pos += 1
                    top[1] = "more"
                    want = "key" if top[0] == "{" else "value"
                elif c == ("}" if top[0] == "{" else "]"):
                    self.pos += 1
                    stack.pop()
                    if not stack:
                        return
                else:
                    raise StreamJsonError("malformed")


def first_element(chunks: Iterable[bytes], key: str, max_element: int = 2 * 1024 * 1024,
                  max_prefix: int = 1024 * 1024) -> Optional[Any]:
    """
    The first element of the top-level array `key` in a JSON object that arrives in `chunks`, or None if the object has
    no such array, or it is empty, or its first element is not an object. Stops reading as soon as that element is
    complete. Raises StreamJsonError("too_large" | "malformed" | "truncated").
    """
    r = _Reader(chunks, max_element, max_prefix)
    if r.skip_ws() != "{":
        raise StreamJsonError("malformed")
    r.pos += 1
    after_comma = False
    while True:
        c = r.skip_ws()
        if c == "}":
            if after_comma:
                raise StreamJsonError("malformed")
            return None
        if c != '"':
            raise StreamJsonError("malformed")
        name = r.read_key()
        if r.skip_ws() != ":":
            raise StreamJsonError("malformed")
        r.pos += 1
        if name == key:
            if r.skip_ws() != "[":
                return None
            r.pos += 1
            if r.skip_ws() == "]":
                return None
            if r.peek() != "{":
                return None
            if len(r.buf[:r.pos].encode("utf-8")) > max_prefix:
                raise StreamJsonError("too_large")
            start = r.anchor = r.pos
            r.skip_value()
            text = r.buf[start:r.pos]
            if len(text.encode("utf-8")) > max_element:
                raise StreamJsonError("too_large")
            try:
                return json.loads(text)
            except (ValueError, RecursionError):
                raise StreamJsonError("malformed") from None
        r.skip_value()
        after_comma = False
        c = r.skip_ws()
        if c == ",":
            r.pos += 1
            after_comma = True
        elif c != "}":
            raise StreamJsonError("malformed")
