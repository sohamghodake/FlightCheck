"""Fault scripts: the ordered steps the mock serves, one per model request (plan §18.2)."""

from __future__ import annotations

import threading
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr


class MockAction(StrEnum):
    OK_TEXT = "ok_text"  # complete text answer
    TOOL_CALL = "tool_call"  # function call for the first function tool in the request
    TRUNCATE = "truncate"  # chat finish_reason "length" / responses status "incomplete"
    TRUNCATE_TOOL_CALL = "truncate_tool_call"  # partial function call in a truncated response
    HTTP_ERROR = "http_error"  # status + OpenAI-style error body
    STREAM_FAIL_AFTER = "stream_fail_after"  # N deltas then response.failed (non-stream: 500)
    KEEPALIVE_STREAM = "keepalive_stream"  # SSE ":" comments between deltas (non-stream: text)
    DELAY = "delay"  # sleep N s, then OK_TEXT (timeout testing)


class MockStep(BaseModel):
    """One scripted answer.

    Common ``params``: ``scenario`` (ID used in markers and call IDs, default ``"mock"``),
    ``text`` (answer text). ``HTTP_ERROR``: ``status`` (default 503), ``message``, ``code``,
    ``retry_after``. ``DELAY``: ``seconds`` (default 1.0). ``STREAM_FAIL_AFTER``: ``n``
    (default 2). Any action answering a stream request: ``interval_s`` (seconds between SSE
    chunks, default 0, at most 60 per gap and 600 in total).
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    action: MockAction
    params: dict[str, Any] = Field(default_factory=dict)


class FaultScript(BaseModel):
    """Steps consumed one per model request; ``default_step`` once the list is used up."""

    model_config = ConfigDict(extra="forbid")

    steps: list[MockStep] = Field(default_factory=list)
    default_step: MockStep = Field(
        default_factory=lambda: MockStep(id="default", action=MockAction.OK_TEXT)
    )

    _lock: threading.Lock = PrivateAttr(default_factory=threading.Lock)
    _cursor: int = PrivateAttr(default=0)

    def next_step(self) -> MockStep:
        """Return the next scripted step (thread-safe); ``default_step`` after the last one."""
        with self._lock:
            if self._cursor < len(self.steps):
                step = self.steps[self._cursor]
                self._cursor += 1
                return step
            return self.default_step

    @property
    def consumed(self) -> int:
        """How many scripted steps (not counting default-step answers) have been served."""
        with self._lock:
            return self._cursor

    def reset(self) -> None:
        with self._lock:
            self._cursor = 0
