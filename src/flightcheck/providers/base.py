"""Normalised provider types and the adapter interface (plan §14.3).

``Endpoint`` is the core enum (``responses``, ``chat.completions``, ``models``) shared with the
scanner and the mock (ADR-027), not a second provider-only enum (ADR-016).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from flightcheck.core.capabilities import Capability, CapabilityProfile
from flightcheck.core.models import Endpoint

__all__ = [
    "TERMINAL_KINDS",
    "ChatRequest",
    "Endpoint",
    "FinishState",
    "ModelInfo",
    "NormalizedResponse",
    "ProviderAdapter",
    "RawResponse",
    "ResponsesRequest",
    "StreamEvent",
    "ToolCall",
    "Usage",
    "chat_finish_state",
    "responses_finish_state",
]


class FinishState(StrEnum):
    COMPLETE = "complete"  # chat "stop" / responses "completed" without function calls
    TOOL_CALLS = "tool_calls"  # chat "tool_calls" / responses "completed" with function calls
    TRUNCATED = "truncated"  # chat "length" / responses "incomplete"
    FAILED = "failed"  # responses "failed" / stream response.failed
    UNKNOWN = "unknown"  # anything else; never treated as complete


def chat_finish_state(finish_reason: Any, has_tool_calls: bool) -> FinishState:
    """Chat Completions ``finish_reason`` → :class:`FinishState`.

    ``length`` is documented (output budget exhausted, even with HTTP 200). ``stop`` and
    ``tool_calls`` follow OpenAI ([UNVERIFIED] for BharatCode). ``stop`` together with tool
    calls (OpenAI does this when ``tool_choice`` forces a function) counts as ``TOOL_CALLS``:
    the calls are finished. Anything else, including a missing value, is ``UNKNOWN``.
    """
    if finish_reason == "length":
        return FinishState.TRUNCATED
    if finish_reason == "tool_calls":
        return FinishState.TOOL_CALLS if has_tool_calls else FinishState.UNKNOWN
    if finish_reason == "stop":
        return FinishState.TOOL_CALLS if has_tool_calls else FinishState.COMPLETE
    return FinishState.UNKNOWN


def responses_finish_state(status: Any, has_function_calls: bool) -> FinishState:
    """Responses ``status`` → :class:`FinishState`. ``incomplete`` is documented (cut short
    even with HTTP 200); ``completed`` and ``failed`` follow OpenAI ([UNVERIFIED] for
    BharatCode). Anything else
    (``in_progress``, ``cancelled``, missing) is ``UNKNOWN``."""
    if status == "completed":
        return FinishState.TOOL_CALLS if has_function_calls else FinishState.COMPLETE
    if status == "incomplete":
        return FinishState.TRUNCATED
    if status == "failed":
        return FinishState.FAILED
    return FinishState.UNKNOWN


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str
    name: str
    arguments_raw: str  # exactly as received; never pre-parsed
    complete: bool  # False whenever the parent response is TRUNCATED/FAILED/UNKNOWN


class Usage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None


class NormalizedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    endpoint: Endpoint
    model: str
    text: str
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_state: FinishState
    finish_detail: str | None  # raw finish_reason / incomplete_details.reason / status
    usage: Usage | None = None
    http_status: int
    request_id: str | None  # from the x-request-id response header
    raw: dict[str, Any]  # redacted raw body, plus "_flightcheck_attempts"
    # Capabilities this request used whose level is "unknown" for the provider, so reports can
    # say "behaviour on this provider is not verified" (plan §14.1).
    unverified_capabilities: list[Capability] = Field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return self.finish_state in (FinishState.COMPLETE, FinishState.TOOL_CALLS)

    def executable_tool_calls(self) -> list[ToolCall]:
        """BharatCode: "do not execute incomplete tool calls"."""
        if self.finish_state is not FinishState.TOOL_CALLS:
            return []
        return [call for call in self.tool_calls if call.complete]


StreamKind = Literal[
    "text_delta", "tool_call_delta", "completed", "incomplete", "failed", "keepalive", "unknown"
]
TERMINAL_KINDS = frozenset({"completed", "incomplete", "failed"})


class StreamEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: StreamKind
    raw_event: str | None  # SSE "event:" name or chunk type
    text: str = ""  # only text_delta carries text; keepalives never do
    data: dict[str, Any] = Field(default_factory=dict)

    @property
    def terminal(self) -> bool:
        return self.kind in TERMINAL_KINDS


class ResponsesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    input: str | list[dict[str, Any]]
    instructions: str | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    text_format: dict[str, Any] | None = None  # serialised as {"text": {"format": ...}}
    max_output_tokens: int | None = None
    reasoning_effort: str | None = None  # serialised as {"reasoning": {"effort": ...}}
    store: bool = False
    extra_body: dict[str, Any] = Field(default_factory=dict)  # probes only


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    max_tokens: int | None = None
    reasoning_effort: str | None = None
    extra_body: dict[str, Any] = Field(default_factory=dict)


class ModelInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    raw: dict[str, Any] = Field(default_factory=dict)


class RawResponse(BaseModel):
    """An HTTP answer exactly as received (redacted), for the Phase 10 probes. HTTP errors are
    returned here, not raised, because probes record them."""

    model_config = ConfigDict(extra="forbid")

    http_status: int
    request_id: str | None
    headers: dict[str, str]  # redacted values, lower-case names
    body: Any = None  # redacted parsed JSON, or None when the body is not JSON
    text: str  # redacted body text


class ProviderAdapter(ABC):
    """Synchronous provider client (ADR-002, ADR-016). Every error an operation raises is a
    :class:`~flightcheck.core.errors.ProviderError` with a redacted message."""

    name: str
    profile: CapabilityProfile

    @abstractmethod
    def list_models(self) -> list[ModelInfo]: ...

    @abstractmethod
    def create_response(self, req: ResponsesRequest) -> NormalizedResponse: ...

    @abstractmethod
    def stream_response(self, req: ResponsesRequest) -> Iterator[StreamEvent]: ...

    @abstractmethod
    def create_chat_completion(self, req: ChatRequest) -> NormalizedResponse: ...

    @abstractmethod
    def stream_chat_completion(self, req: ChatRequest) -> Iterator[StreamEvent]: ...

    def preflight(self, payload: dict[str, Any], endpoint: Endpoint) -> list[str]:
        """Return contract violations for this provider; empty list = OK. Default: none."""
        return []

    def close(self) -> None:  # noqa: B027 - optional hook, deliberately not abstract
        return None

    def __enter__(self) -> ProviderAdapter:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
