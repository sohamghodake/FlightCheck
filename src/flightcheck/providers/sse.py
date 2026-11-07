"""Server-sent events: a small parser and the BharatCode stream mappings (plan §14.6).

Parsing follows the WHATWG SSE rules: lines end at CRLF, LF or CR; a line starting with ``:``
is a comment; ``field: value`` lines (one leading space stripped); ``data`` lines join with
``\\n``; a blank line dispatches; an event with no ``data`` is not dispatched; an unterminated
event at end of stream is discarded.

Comments become ``keepalive`` events whatever their text: BharatCode documents queue keepalive
comments that are not model output ([VERIFIED]); their exact text is [UNVERIFIED].

:func:`iter_sse_lines` splits decoded text on the three SSE line endings only. httpx2's
``Response.iter_lines()`` also works with :func:`parse_sse`, but it uses ``str.splitlines``,
which also splits on U+2028, U+0085 and other characters that may appear unescaped inside JSON
strings, so the adapter feeds ``iter_text()`` through :func:`iter_sse_lines` instead (ADR-016).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from flightcheck.providers.base import StreamEvent

__all__ = [
    "EOF_REASON",
    "SSEMessage",
    "SSEProtocolError",
    "chat_events",
    "iter_sse_lines",
    "parse_sse",
    "responses_events",
]

EOF_REASON = "eof_without_terminal_event"
DONE_WITHOUT_FINISH_REASON = "done_without_finish_reason"
UNRECOGNISED_FINISH_REASON = "unrecognised_finish_reason"
ERROR_CHUNK_REASON = "error_chunk"
_LINE_END = re.compile(r"\r\n|\r|\n")


class SSEProtocolError(ValueError):
    """A stream event whose data is not what the protocol requires (e.g. invalid JSON)."""


@dataclass(frozen=True)
class SSEMessage:
    """One dispatched SSE event, or a comment (``comment`` is not None, ``data`` empty)."""

    event: str | None = None
    data: str = ""
    comment: str | None = None

    @property
    def is_comment(self) -> bool:
        return self.comment is not None


def iter_sse_lines(chunks: Iterable[str]) -> Iterator[str]:
    """Split decoded text chunks into lines on CRLF, LF or CR only (SSE line endings).

    A CR ends its line at once; an LF that starts the next chunk is then skipped, so a CRLF
    split across chunks is one line ending. A final line without a line ending is yielded at
    the end. Each chunk is scanned once.
    """
    pending: list[str] = []
    skip_lf = False
    for chunk in chunks:
        if not chunk:
            continue
        if skip_lf:
            skip_lf = False
            if chunk[0] == "\n":
                chunk = chunk[1:]
                if not chunk:
                    continue
        parts = _LINE_END.split(chunk)
        if len(parts) == 1:
            pending.append(chunk)
            continue
        yield "".join(pending) + parts[0]
        pending = []
        yield from parts[1:-1]
        if parts[-1]:
            pending.append(parts[-1])
        skip_lf = chunk.endswith("\r")
    if pending:
        yield "".join(pending)


def parse_sse(lines: Iterable[str]) -> Iterator[SSEMessage]:
    """Parse SSE lines (without line endings) into messages and comments."""
    event: str | None = None
    data: list[str] = []
    first = True
    for line in lines:
        if first:
            first = False
            line = line.removeprefix("﻿")
        if not line:
            if data:
                yield SSEMessage(event=event, data="\n".join(data))
            event, data = None, []
            continue
        if line.startswith(":"):
            yield SSEMessage(comment=line[1:].removeprefix(" "))
            continue
        name, sep, value = line.partition(":")
        if sep:
            value = value.removeprefix(" ")
        if name == "event":
            event = value or None
        elif name == "data":
            data.append(value)
        # "id", "retry" and unknown fields are ignored (no reconnection in FlightCheck).
    # End of stream: an event without its blank line is discarded (SSE rules).


def _json_object(message: SSEMessage) -> dict[str, Any]:
    try:
        value = json.loads(message.data)
    except ValueError as exc:
        raise SSEProtocolError(f"stream event data is not valid JSON ({exc.msg})") from None
    if not isinstance(value, dict):
        raise SSEProtocolError("stream event data is not a JSON object")
    return value


def _keepalive(message: SSEMessage) -> StreamEvent:
    return StreamEvent(kind="keepalive", raw_event=None, data={"comment": message.comment})


def _synthetic_failed(reason: str, **extra: Any) -> StreamEvent:
    return StreamEvent(kind="failed", raw_event=None, data={"reason": reason, **extra})


# --- Responses ------------------------------------------------------------------------------

# Event names are [VERIFIED] (BHARATCODE_CONTRACT.md §15.1); payload fields follow OpenAI's
# Responses event shapes and are [UNVERIFIED] for BharatCode (ADR-015).
_RESPONSES_TERMINAL = {
    "response.completed": "completed",
    "response.incomplete": "incomplete",
    "response.failed": "failed",
}


def responses_events(messages: Iterable[SSEMessage]) -> Iterator[StreamEvent]:
    """Map a Responses SSE stream to :class:`StreamEvent` (plan §14.6 table).

    Stops after the first terminal event. If the stream ends without one, yields a synthetic
    ``failed`` event with ``data["reason"] == "eof_without_terminal_event"``.
    """
    for message in messages:
        if message.is_comment:
            yield _keepalive(message)
            continue
        data = _json_object(message)
        name = message.event or (data.get("type") if isinstance(data.get("type"), str) else None)
        if name == "response.output_text.delta":
            delta = data.get("delta")
            if not isinstance(delta, str):
                raise SSEProtocolError("response.output_text.delta has no string delta")
            yield StreamEvent(kind="text_delta", raw_event=name, text=delta, data=data)
        elif name in _RESPONSES_TERMINAL:
            yield StreamEvent(kind=_RESPONSES_TERMINAL[name], raw_event=name, data=data)
            return
        elif name == "response.function_call_arguments.delta":
            yield StreamEvent(kind="tool_call_delta", raw_event=name, data=data)
        else:
            yield StreamEvent(kind="unknown", raw_event=name, data=data)
    yield _synthetic_failed(EOF_REASON)


# --- Chat Completions -----------------------------------------------------------------------

# The whole Chat stream format (data: {chunk} lines, data: [DONE]) is [UNVERIFIED] for
# BharatCode: not documented; OpenAI's convention is followed (ADR-015).
_CHAT_FINISH = {"stop": "completed", "tool_calls": "completed", "length": "incomplete"}


def _first_choice(chunk: dict[str, Any]) -> dict[str, Any] | None:
    choices = chunk.get("choices")
    if not isinstance(choices, list):
        return None
    for choice in choices:
        if isinstance(choice, dict) and choice.get("index", 0) == 0:
            return choice
    return None


def chat_events(messages: Iterable[SSEMessage]) -> Iterator[StreamEvent]:
    """Map a Chat Completions SSE stream to :class:`StreamEvent` (plan §14.6).

    ``choices[0].delta.content`` → ``text_delta``; ``delta.tool_calls`` → ``tool_call_delta``;
    ``finish_reason`` ``stop``/``tool_calls`` → ``completed``, ``length`` → ``incomplete``
    (terminal; the stream stops there). Synthetic ``failed`` events: ``[DONE]`` before any
    finish_reason, EOF before any finish_reason, an unrecognised finish_reason, or a chunk with
    a top-level ``error`` (an OpenAI-compatible server convention, [UNVERIFIED]).
    """
    for message in messages:
        if message.is_comment:
            yield _keepalive(message)
            continue
        if message.data.strip() == "[DONE]":
            yield _synthetic_failed(DONE_WITHOUT_FINISH_REASON)
            return
        chunk = _json_object(message)
        raw_event = chunk.get("object") if isinstance(chunk.get("object"), str) else "chunk"
        if "error" in chunk:
            yield StreamEvent(
                kind="failed", raw_event=raw_event, data={"reason": ERROR_CHUNK_REASON, **chunk}
            )
            return
        choice = _first_choice(chunk)
        if choice is None:
            yield StreamEvent(kind="unknown", raw_event=raw_event, data=chunk)
            continue
        delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
        emitted = False
        content = delta.get("content")
        if isinstance(content, str) and content:
            yield StreamEvent(kind="text_delta", raw_event=raw_event, text=content, data=chunk)
            emitted = True
        if delta.get("tool_calls"):
            yield StreamEvent(kind="tool_call_delta", raw_event=raw_event, data=chunk)
            emitted = True
        finish = choice.get("finish_reason")
        if finish is not None:
            kind = _CHAT_FINISH.get(finish) if isinstance(finish, str) else None
            if kind is None:
                yield _synthetic_failed(
                    UNRECOGNISED_FINISH_REASON, finish_reason=finish, chunk=chunk
                )
            else:
                yield StreamEvent(kind=kind, raw_event=raw_event, data=chunk)  # type: ignore[arg-type]
            return
        if not emitted:
            yield StreamEvent(kind="unknown", raw_event=raw_event, data=chunk)
    yield _synthetic_failed(EOF_REASON)
