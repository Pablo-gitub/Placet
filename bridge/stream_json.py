"""
Reads one value out of a huge JSON answer without downloading the rest: the first element of a top-level array.

Antigravity's GetCascadeTrajectorySteps answers with every step from `stepOffset` to the end of the conversation (12 MB
for a few dozen steps in a real case), and the step we want is the first. The answer is read as a stream and the read
stops as soon as that element is complete.

This is not a bracket counter: the scanner knows strings (escapes, braces and brackets inside them), splits that fall in
the middle of a UTF-8 sequence, fields that come before the array (their own `"steps"` included), and nested values; the
element it finds is then parsed by `json.loads`, so what it returns is valid JSON or an error.
"""
import codecs
import json
import re
from typing import Any, Iterable, Optional

_WS = " \t\r\n"
_STRING_STOP = re.compile(r'["\\]')
_STRUCTURE = re.compile(r'["{}\[\]]')
_SCALAR_END = re.compile(r'[,}\]\s]')
_ESCAPES = '"\\/bfnrtu'


class StreamJsonError(Exception):
    """`kind`: "too_large" (the element is beyond the cap), "malformed" (not JSON, or not UTF-8) or "truncated" (cut short)."""

    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


class _Reader:
    def __init__(self, chunks: Iterable[bytes], max_element: int, max_prefix: int):
        self.chunks = iter(chunks)
        self.decoder = codecs.getincrementaldecoder("utf-8")()
        self.buf = ""
        self.pos = 0
        self.max_element, self.max_prefix = max_element, max_prefix
        self.limit = max_prefix              # characters we may hold: before the element starts, then the element itself
        self.anchor = None                   # where the element starts in `buf`, once it has
        self.eof = False

    def more(self) -> None:
        """Appends the next chunk; raises "truncated" if the answer ended, "too_large" beyond the cap."""
        # Before the element starts we hold at most `max_prefix` characters (what precedes `steps`), then at most the
        # element itself. Nothing is trimmed: positions stay valid while a string or a value is being skipped.
        if len(self.buf) - (0 if self.anchor is None else self.anchor) > self.limit:
            raise StreamJsonError("too_large")
        try:
            chunk = next(self.chunks)
        except StopIteration:
            try:
                tail = self.decoder.decode(b"", final=True)
            except UnicodeDecodeError:
                raise StreamJsonError("malformed") from None
            self.eof = True
            if tail:
                self.buf += tail
                return
            raise StreamJsonError("truncated") from None
        try:
            self.buf += self.decoder.decode(chunk)
        except UnicodeDecodeError:
            raise StreamJsonError("malformed") from None

    def peek(self) -> str:
        while self.pos >= len(self.buf):
            self.more()
        return self.buf[self.pos]

    def skip_ws(self) -> str:
        while True:
            c = self.peek()
            if c not in _WS:
                return c
            self.pos += 1

    def skip_string(self) -> None:
        """At an opening quote: past the closing one."""
        self.pos += 1
        while True:
            m = _STRING_STOP.search(self.buf, self.pos)
            if m is None:
                self.pos = len(self.buf)
                self.more()
                continue
            self.pos = m.end()
            if m.group() == '"':
                return
            # a backslash: the next character belongs to it
            while self.pos >= len(self.buf):
                self.more()
            if self.buf[self.pos] not in _ESCAPES:
                raise StreamJsonError("malformed")
            self.pos += 1

    def read_key(self) -> str:
        """At an opening quote: the key, decoded."""
        start = self.pos
        self.skip_string()
        try:
            key = json.loads(self.buf[start:self.pos])
        except ValueError:
            raise StreamJsonError("malformed") from None
        return key if isinstance(key, str) else ""

    def skip_value(self) -> None:
        """Past one JSON value of any kind, string-aware."""
        c = self.skip_ws()
        if c == '"':
            self.skip_string()
        elif c in "{[":
            self.skip_container()
        else:
            start = self.pos
            while True:
                m = _SCALAR_END.search(self.buf, self.pos)
                if m is not None:
                    if m.start() == start:           # no value at all, e.g. "key":,
                        raise StreamJsonError("malformed")
                    self.pos = m.start()
                    return
                self.pos = len(self.buf)
                self.more()

    def skip_container(self) -> int:
        """At `{` or `[`: past its matching closer. Returns the characters it spans."""
        start, depth = self.pos, 0
        while True:
            m = _STRUCTURE.search(self.buf, self.pos)
            if m is None:
                self.pos = len(self.buf)
                self.more()
                continue
            char = m.group()
            self.pos = m.start()
            if char == '"':
                self.skip_string()
                continue
            self.pos += 1
            depth += 1 if char in "{[" else -1
            if depth == 0:
                return self.pos - start
            if depth < 0:
                raise StreamJsonError("malformed")
            if self.pos - start > self.limit:
                raise StreamJsonError("too_large")


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
    while True:
        c = r.skip_ws()
        if c == "}":
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
            r.limit = max_element
            start = r.anchor = r.pos
            r.skip_container()
            text = r.buf[start:r.pos]
            if len(text.encode("utf-8")) > max_element:
                raise StreamJsonError("too_large")
            try:
                return json.loads(text)
            except ValueError:
                raise StreamJsonError("malformed") from None
        r.skip_value()
        c = r.skip_ws()
        if c == ",":
            r.pos += 1
        elif c != "}":
            raise StreamJsonError("malformed")
