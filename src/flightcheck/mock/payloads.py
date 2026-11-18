"""Pure payload builders for the mock (plan §18.2). No FastAPI imports: unit-testable.

Action builders take ``(request_json, step, seq)`` and return ``(status, headers, body)``.
Shapes follow docs/BHARATCODE_CONTRACT.md §15.1 where it is specific ("OpenAI-compatible Chat
Completions or Responses"; truncation fields) and OpenAI conventions elsewhere, carrying only
the fields the OpenAI SDK models and FlightCheck's validators need. Everything beyond the
documented fields is [UNVERIFIED] for BharatCode.

Groq routes (``/openai/v1/...``, ADR-038) reuse the same builders and add Groq's shapes from
docs/GROQ_CONTRACT.md: Chat bodies gain ``x_groq.id``, the ``usage`` timing fields,
``system_fingerprint`` and ``service_tier`` (C13, C14); errors are ``{"error": {"message",
"type"}}`` (E1, no ``code``); Chat streams carry ``x_groq`` in the first and final chunk (ST2) and
fail with an ``x_groq.error`` chunk (ST3). No ``x-request-id`` header is sent on Groq routes
(E10: not documented; the request ID is ``x_groq.id``).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from flightcheck.core.config import (
    BHARATCODE_DEFAULT_MODEL,
    BHARATCODE_DOCUMENTED_MODELS,
    GROQ_DEFAULT_MODEL,
    GROQ_DOCUMENTED_MODELS,
)
from flightcheck.mock.faults import MockAction, MockStep

Headers = dict[str, str]
Built = tuple[int, Headers, dict[str, Any]]
Builder = Callable[[dict[str, Any], MockStep, int], Built]

# Fixed timestamp (2026-11-06T00:00:00Z) so every payload is deterministic.
MOCK_CREATED_AT = 1_793_923_200
DEFAULT_SCENARIO = "mock"
PLACEHOLDER_STRING = "fc_value"
_SCHEMA_DEPTH_LIMIT = 8
_USAGE_INPUT_TOKENS = 12


# --- Shared helpers -------------------------------------------------------------------------


def request_id(seq: int) -> str:
    return f"fc_mock_{seq}"


def base_headers(seq: int) -> Headers:
    """``x-request-id`` is documented (§15.1 "Support"); its value format is the mock's own."""
    return {"x-request-id": request_id(seq)}


def scenario_of(step: MockStep) -> str:
    value = step.params.get("scenario")
    return value if isinstance(value, str) and value else DEFAULT_SCENARIO


def call_id(scenario: str, n: int) -> str:
    return f"call_fc_{scenario}_{n}"


def answer_text(step: MockStep) -> str:
    text = step.params.get("text")
    return text if isinstance(text, str) else f"FlightCheck mock answer [{scenario_of(step)}]"


def partial_text(step: MockStep) -> str:
    text = step.params.get("text")
    return text if isinstance(text, str) else f"FlightCheck partial [{scenario_of(step)}]"


def model_of(request_json: dict[str, Any]) -> str:
    """Echo the requested model. ``model`` is required by the contract; what BharatCode does
    without it is [UNVERIFIED], so the mock answers with the default model rather than guess."""
    model = request_json.get("model")
    return model if isinstance(model, str) and model else BHARATCODE_DEFAULT_MODEL


def _word_tokens(text: str) -> int:
    return max(1, len(text.split()))


# --- Function tools and argument generation -------------------------------------------------


def find_function_tool(request_json: dict[str, Any]) -> tuple[str, dict[str, Any], str] | None:
    """First function tool in ``tools``: ``(name, parameters_schema, style)`` or ``None``.

    Both definition styles are accepted: Responses (``{"type": "function", "name": ...}``) and
    Chat (``{"type": "function", "function": {"name": ...}}``). ``style`` is ``"responses"`` or
    ``"chat"`` so runtime checks can tell which one the app sent. Whether BharatCode accepts the
    Chat style on /responses (or the reverse) is [UNVERIFIED].
    """
    tools = request_json.get("tools")
    if not isinstance(tools, list):
        return None
    for tool in tools:
        if not isinstance(tool, dict) or tool.get("type") != "function":
            continue
        if isinstance(tool.get("name"), str) and tool["name"]:
            params = tool.get("parameters")
            return tool["name"], params if isinstance(params, dict) else {}, "responses"
        function = tool.get("function")
        if isinstance(function, dict) and isinstance(function.get("name"), str):
            if function["name"]:
                params = function.get("parameters")
                return function["name"], params if isinstance(params, dict) else {}, "chat"
    return None


def _schema_type(schema: dict[str, Any]) -> str | None:
    kind = schema.get("type")
    if isinstance(kind, list):  # e.g. ["string", "null"]
        kind = next((k for k in kind if isinstance(k, str) and k != "null"), None)
    return kind if isinstance(kind, str) else None


def value_for_schema(schema: Any, depth: int = 0) -> Any:
    """A minimal value that satisfies ``schema`` (enum first, then type)."""
    if not isinstance(schema, dict) or depth > _SCHEMA_DEPTH_LIMIT:
        return PLACEHOLDER_STRING
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return enum[0]
    if "const" in schema:
        return schema["const"]
    kind = _schema_type(schema)
    if kind == "string":
        return PLACEHOLDER_STRING
    if kind in ("number", "integer"):
        return 1
    if kind == "boolean":
        return True
    if kind == "array":
        return []
    if kind == "null":
        return None
    if kind == "object" or "properties" in schema:
        return arguments_for_schema(schema, depth + 1)
    return PLACEHOLDER_STRING


def arguments_for_schema(schema: Any, depth: int = 0) -> dict[str, Any]:
    """Arguments for a tool's JSON Schema: required properties only, in schema order."""
    if not isinstance(schema, dict):
        return {}
    properties = schema.get("properties")
    required = schema.get("required")
    if not isinstance(properties, dict) or not isinstance(required, list):
        return {}
    return {
        name: value_for_schema(properties[name], depth)
        for name in required
        if isinstance(name, str) and name in properties
    }


def _tool_call_parts(
    request_json: dict[str, Any], step: MockStep, seq: int
) -> tuple[str, str, str, str] | None:
    """``(call_id, name, arguments_json, style)`` for the first function tool, or ``None``."""
    found = find_function_tool(request_json)
    if found is None:
        return None
    name, schema, style = found
    arguments = json.dumps(arguments_for_schema(schema), separators=(",", ":"))
    return call_id(scenario_of(step), seq), name, arguments, style


def cut_arguments(arguments: str) -> str:
    """Truncated arguments: a prefix that is never valid JSON (always drops the closing brace)."""
    return arguments[: max(1, len(arguments) // 2)] if len(arguments) > 2 else "{"


# --- Responses ------------------------------------------------------------------------------


def _responses_usage(output_tokens: int) -> dict[str, Any]:
    # Detail objects are required by the OpenAI SDK model; their values are [UNVERIFIED].
    return {
        "input_tokens": _USAGE_INPUT_TOKENS,
        "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
        "output_tokens": output_tokens,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": _USAGE_INPUT_TOKENS + output_tokens,
    }


def _message_item(seq: int, text: str, status: str) -> dict[str, Any]:
    return {
        "type": "message",
        "id": f"msg_fc_mock_{seq}",
        "status": status,
        "role": "assistant",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def _function_call_item(
    seq: int, call: str, name: str, arguments: str, status: str
) -> dict[str, Any]:
    return {
        "type": "function_call",
        "id": f"fc_fc_mock_{seq}",
        "call_id": call,
        "name": name,
        "arguments": arguments,
        "status": status,
    }


def _response_object(
    request_json: dict[str, Any],
    seq: int,
    output: list[dict[str, Any]],
    output_tokens: int,
    *,
    incomplete: bool,
) -> dict[str, Any]:
    return {
        "id": f"resp_fc_mock_{seq}",
        "object": "response",
        "created_at": MOCK_CREATED_AT,
        "model": model_of(request_json),
        # Documented (§15.1 "Truncation (Responses)"): incomplete + max_output_tokens.
        "status": "incomplete" if incomplete else "completed",
        "incomplete_details": {"reason": "max_output_tokens"} if incomplete else None,
        "output": output,
        "usage": _responses_usage(output_tokens),
        # Required by the OpenAI SDK model; BharatCode's values are [UNVERIFIED]. The request's
        # tools are not echoed back, so either definition style parses.
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
        "store": False,
    }


def responses_text(request_json: dict[str, Any], step: MockStep, seq: int) -> Built:
    text = answer_text(step)
    body = _response_object(
        request_json,
        seq,
        [_message_item(seq, text, "completed")],
        _word_tokens(text),
        incomplete=False,
    )
    return 200, base_headers(seq), body


def responses_tool_call(request_json: dict[str, Any], step: MockStep, seq: int) -> Built:
    """Function call for the first function tool; plain text when the request has none."""
    parts = _tool_call_parts(request_json, step, seq)
    if parts is None:
        return responses_text(request_json, step, seq)
    call, name, arguments, _style = parts
    item = _function_call_item(seq, call, name, arguments, "completed")
    body = _response_object(request_json, seq, [item], _word_tokens(arguments), incomplete=False)
    return 200, base_headers(seq), body


def responses_truncated(request_json: dict[str, Any], step: MockStep, seq: int) -> Built:
    text = partial_text(step)
    body = _response_object(
        request_json,
        seq,
        [_message_item(seq, text, "incomplete")],
        _word_tokens(text),
        incomplete=True,
    )
    return 200, base_headers(seq), body


def responses_truncated_tool_call(request_json: dict[str, Any], step: MockStep, seq: int) -> Built:
    """A function call cut off mid-arguments inside an incomplete response."""
    parts = _tool_call_parts(request_json, step, seq)
    if parts is None:
        return responses_truncated(request_json, step, seq)
    call, name, arguments, _style = parts
    partial = cut_arguments(arguments)
    item = _function_call_item(seq, call, name, partial, "incomplete")
    body = _response_object(request_json, seq, [item], _word_tokens(partial), incomplete=True)
    return 200, base_headers(seq), body


# --- Chat Completions -----------------------------------------------------------------------


def _chat_object(
    request_json: dict[str, Any],
    seq: int,
    message: dict[str, Any],
    finish_reason: str,
    completion_tokens: int,
) -> dict[str, Any]:
    return {
        "id": f"chatcmpl-fc_mock_{seq}",
        "object": "chat.completion",
        "created": MOCK_CREATED_AT,
        "model": model_of(request_json),
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {
            "prompt_tokens": _USAGE_INPUT_TOKENS,
            "completion_tokens": completion_tokens,
            "total_tokens": _USAGE_INPUT_TOKENS + completion_tokens,
        },
    }


def _chat_tool_message(call: str, name: str, arguments: str) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": call, "type": "function", "function": {"name": name, "arguments": arguments}}
        ],
    }


def chat_text(request_json: dict[str, Any], step: MockStep, seq: int) -> Built:
    text = answer_text(step)
    message = {"role": "assistant", "content": text}
    body = _chat_object(request_json, seq, message, "stop", _word_tokens(text))
    return 200, base_headers(seq), body


def chat_tool_call(request_json: dict[str, Any], step: MockStep, seq: int) -> Built:
    parts = _tool_call_parts(request_json, step, seq)
    if parts is None:
        return chat_text(request_json, step, seq)
    call, name, arguments, _style = parts
    message = _chat_tool_message(call, name, arguments)
    body = _chat_object(request_json, seq, message, "tool_calls", _word_tokens(arguments))
    return 200, base_headers(seq), body


def chat_truncated(request_json: dict[str, Any], step: MockStep, seq: int) -> Built:
    text = partial_text(step)
    message = {"role": "assistant", "content": text}
    # Documented (§15.1 "Truncation (Chat)"): finish_reason "length" with HTTP 200.
    body = _chat_object(request_json, seq, message, "length", _word_tokens(text))
    return 200, base_headers(seq), body


def chat_truncated_tool_call(request_json: dict[str, Any], step: MockStep, seq: int) -> Built:
    parts = _tool_call_parts(request_json, step, seq)
    if parts is None:
        return chat_truncated(request_json, step, seq)
    call, name, arguments, _style = parts
    partial = cut_arguments(arguments)
    message = _chat_tool_message(call, name, partial)
    body = _chat_object(request_json, seq, message, "length", _word_tokens(partial))
    return 200, base_headers(seq), body


# --- Errors and models ----------------------------------------------------------------------

# OpenAI error "type" by status. BharatCode's error body shape and codes are [UNVERIFIED]
# (BHARATCODE_CONTRACT.md §15.1, "Not documented").
_ERROR_TYPES = {
    400: "invalid_request_error",
    401: "authentication_error",
    403: "permission_error",
    404: "not_found_error",
    429: "rate_limit_error",
}


def error_type_for(status: int) -> str:
    if status in _ERROR_TYPES:
        return _ERROR_TYPES[status]
    return "server_error" if status >= 500 else "invalid_request_error"


def error_body(status: int, message: str, code: str | None) -> dict[str, Any]:
    """OpenAI-style ``{"error": {"message", "type", "code"}}`` (shape [UNVERIFIED])."""
    return {"error": {"message": message, "type": error_type_for(status), "code": code}}


def http_error(request_json: dict[str, Any], step: MockStep, seq: int) -> Built:
    """``HTTP_ERROR``: ``status`` (default 503, documented as "model busy"), message, code,
    optional ``headers`` (``retry-after`` / ``x-ratelimit-*``, see :func:`error_headers`)."""
    status = step.params.get("status", 503)
    if isinstance(status, bool) or not isinstance(status, int) or not 400 <= status <= 599:
        raise ValueError("HTTP_ERROR step needs an integer status between 400 and 599")
    message = step.params.get("message")
    if not isinstance(message, str):
        message = "Model is busy, retry in a few seconds." if status == 503 else "Mock error."
    code = step.params.get("code")
    headers = {**base_headers(seq), **error_headers(step)}
    retry_after = step.params.get("retry_after")
    if retry_after is not None:
        # Whether BharatCode sends Retry-After is [UNVERIFIED]; scripts opt in.
        headers["retry-after"] = str(retry_after)
    return status, headers, error_body(status, message, code if isinstance(code, str) else None)


def stream_failure(request_json: dict[str, Any], step: MockStep, seq: int) -> Built:
    """``STREAM_FAIL_AFTER`` on a non-stream request: the model fails, so HTTP 500 (§18.2).
    The code is [UNVERIFIED]."""
    return 500, base_headers(seq), error_body(500, "The model failed mid-response.", "server_error")


def models_list() -> dict[str, Any]:
    """``GET /models``: only the documented model IDs. ``created``/``owned_by`` are
    [UNVERIFIED] (OpenAI's model-object shape)."""
    return {
        "object": "list",
        "data": [
            {"id": model, "object": "model", "created": MOCK_CREATED_AT, "owned_by": "bharatcode"}
            for model in sorted(BHARATCODE_DOCUMENTED_MODELS)
        ],
    }


# --- Groq shapes (ADR-038) ------------------------------------------------------------------

GROQ_REQUEST_ID_PREFIX = "req_fc_mock_"  # x_groq.id; Groq's IDs look like "req_…" (C13)
GROQ_SYSTEM_FINGERPRINT = "fp_fc_mock"  # field documented (C13); value is the mock's own
GROQ_SERVICE_TIER = "on_demand"  # the documented default tier (C14)
# Documented usage fields (C13), fixed mock values in seconds.
GROQ_USAGE_TIMES = {
    "queue_time": 0.001,
    "prompt_time": 0.002,
    "completion_time": 0.003,
    "total_time": 0.006,
}
# OpenAI-style "type" by status. Groq documents the body shape and the type
# "invalid_request_error" (E1) and names 498's error "capacity_exceeded" (E2); every other type
# value is [UNVERIFIED].
_GROQ_ERROR_TYPES = {
    **_ERROR_TYPES,
    413: "invalid_request_error",
    422: "invalid_request_error",
    498: "capacity_exceeded",
}
# Headers a Groq HTTP_ERROR step may set (contract E5: retry-after only on 429; E6: the six
# x-ratelimit-* headers).
GROQ_RATE_LIMIT_HEADERS = frozenset(
    f"x-ratelimit-{kind}-{unit}"
    for kind in ("limit", "remaining", "reset")
    for unit in ("requests", "tokens")
)
ALLOWED_ERROR_HEADERS = GROQ_RATE_LIMIT_HEADERS | {"retry-after"}


def groq_request_id(seq: int) -> str:
    return f"{GROQ_REQUEST_ID_PREFIX}{seq}"


def groq_headers(headers: Headers) -> Headers:
    """Headers for a Groq-route answer: no ``x-request-id`` (not documented, E10)."""
    return {k: v for k, v in headers.items() if k.lower() != "x-request-id"}


def groq_request(request_json: dict[str, Any]) -> dict[str, Any]:
    """The request as the builders see it on a Groq route: without a usable ``model`` the
    answer names Groq's default model (what Groq does without one is not documented; C1 says
    it is required)."""
    model = request_json.get("model")
    if isinstance(model, str) and model:
        return request_json
    return {**request_json, "model": GROQ_DEFAULT_MODEL}


def groq_usage(usage: dict[str, Any]) -> dict[str, Any]:
    return {**usage, **GROQ_USAGE_TIMES}


def groq_chat_body(body: dict[str, Any], seq: int) -> dict[str, Any]:
    """A Chat Completions body with Groq's documented extra fields (C13, C14)."""
    return {
        **body,
        "usage": groq_usage(body["usage"]),
        "system_fingerprint": GROQ_SYSTEM_FINGERPRINT,
        "service_tier": GROQ_SERVICE_TIER,
        "x_groq": {"id": groq_request_id(seq)},
    }


def groq_error_type_for(status: int) -> str:
    if status in _GROQ_ERROR_TYPES:
        return _GROQ_ERROR_TYPES[status]
    return "server_error" if status >= 500 else "invalid_request_error"


def groq_error_body(status: int, message: str, **extra: Any) -> dict[str, Any]:
    """Groq's error body ``{"error": {"message", "type"}}`` (E1; no ``code`` is documented).
    ``extra`` adds documented fields such as ``failed_generation`` (T6)."""
    return {"error": {"message": message, "type": groq_error_type_for(status), **extra}}


def error_headers(step: MockStep) -> Headers:
    """The ``headers`` step parameter: ``retry-after`` and ``x-ratelimit-*`` only (E5, E6),
    names lower-case, values strings or integers."""
    raw = step.params.get("headers")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("HTTP_ERROR 'headers' must be a mapping of header names to values")
    headers: Headers = {}
    for name, value in raw.items():
        if not isinstance(name, str) or name.lower() not in ALLOWED_ERROR_HEADERS:
            raise ValueError("HTTP_ERROR 'headers' allows only retry-after and x-ratelimit-*")
        if isinstance(value, bool) or not isinstance(value, str | int | float):
            raise ValueError("HTTP_ERROR header values must be strings or numbers")
        headers[name.lower()] = str(value)
    return headers


def _error_status(step: MockStep) -> int:
    status = step.params.get("status", 503)
    if isinstance(status, bool) or not isinstance(status, int) or not 400 <= status <= 599:
        raise ValueError("HTTP_ERROR step needs an integer status between 400 and 599")
    return status


def groq_http_error(request_json: dict[str, Any], step: MockStep, seq: int) -> Built:
    """``HTTP_ERROR`` on a Groq route: Groq's error body (E1) plus the step's ``headers``.

    ``retry-after`` (from ``headers`` or the older ``retry_after`` parameter) is allowed only
    with 429, the only status Groq sets it on (E5). The default 503 message says to wait, as
    Groq's errors page does (E3); it does not claim a "model busy" cause.
    """
    status = _error_status(step)
    message = step.params.get("message")
    if not isinstance(message, str):
        message = "Service unavailable: wait before retrying." if status == 503 else "Mock error."
    headers = error_headers(step)
    retry_after = step.params.get("retry_after")
    if retry_after is not None:
        headers.setdefault("retry-after", str(retry_after))
    if "retry-after" in headers and status != 429:
        raise ValueError("Groq sets retry-after only on 429 (contract E5)")
    return status, headers, groq_error_body(status, message)


def groq_stream_failure(request_json: dict[str, Any], step: MockStep, seq: int) -> Built:
    """``STREAM_FAIL_AFTER`` on a non-stream Groq request: HTTP 500 ("retry later", E3)."""
    return 500, {}, groq_error_body(500, "The model failed mid-response.")


def groq_tool_validation_error(request_json: dict[str, Any], step: MockStep, seq: int) -> Built:
    """``TOOL_VALIDATION_ERROR`` (P1): Groq's 400 for a generated tool call that failed
    validation, with ``error.failed_generation`` (T6). Groq says it carries the reason, the
    ``tool_call_id`` and the attempted arguments; its exact format is [UNVERIFIED], so the mock
    sends a JSON string with those three parts."""
    parts = _tool_call_parts(request_json, step, seq)
    if parts is None:
        name, call = "unknown_tool", call_id(scenario_of(step), seq)
    else:
        call, name = parts[0], parts[1]
    attempted = json.dumps(
        {
            "reason": "tool call validation failed",
            "tool_call_id": call,
            "name": name,
            "arguments": "{",
        },
        separators=(",", ":"),
    )
    body = groq_error_body(
        400,
        "Tool call validation failed (FlightCheck mock); see failed_generation.",
        failed_generation=attempted,
    )
    return 400, {}, body


def groq_models_list() -> dict[str, Any]:
    """``GET /openai/v1/models``: the dated catalogue (§1.8). The field shape is OpenAI's and
    [UNVERIFIED] for Groq (A6); ``owned_by`` is the mock's own value."""
    return {
        "object": "list",
        "data": [
            {"id": model, "object": "model", "created": MOCK_CREATED_AT, "owned_by": "groq"}
            for model in sorted(GROQ_DOCUMENTED_MODELS)
        ],
    }


RESPONSES_BUILDERS: dict[str, Builder] = {
    "text": responses_text,
    "tool_call": responses_tool_call,
    "truncated": responses_truncated,
    "truncated_tool_call": responses_truncated_tool_call,
}
CHAT_BUILDERS: dict[str, Builder] = {
    "text": chat_text,
    "tool_call": chat_tool_call,
    "truncated": chat_truncated,
    "truncated_tool_call": chat_truncated_tool_call,
}


# --- Streaming (Phase 5B) -------------------------------------------------------------------
#
# Event names documented for BharatCode (§15.1 "Responses — streaming"): response.output_text.delta
# carries text; response.completed / response.incomplete end a stream; response.failed is an
# error; keepalive comments are not model output. Every other event name and every payload field
# follows OpenAI's Responses event shapes and is [UNVERIFIED] for BharatCode (ADR-015). The extra
# OpenAI events (output_item.added, content_part.added, ...) are included because the OpenAI
# SDK's ``responses.stream()`` helper needs them to accumulate output.
#
# Chat Completions streaming (``data: {chunk}`` lines and ``data: [DONE]``) is entirely
# [UNVERIFIED] for BharatCode (OpenAI convention).

KEEPALIVE = b": keepalive\n\n"  # the exact comment text BharatCode sends is [UNVERIFIED]
DONE = b"data: [DONE]\n\n"
DEFAULT_FAIL_AFTER = 2
MAX_INTERVAL_S = 60.0  # per gap between chunks
MAX_STREAM_S = 600.0  # all gaps of one stream together
_ARGUMENT_PIECE = 8


@dataclass(frozen=True)
class SSEStream:
    """A complete stream: SSE chunks in order plus the outcome to record when it ends."""

    headers: Headers
    chunks: list[bytes]
    outcome: dict[str, Any]
    interval_s: float = 0.0  # pause between consecutive chunks (slow-stream scenarios)


def sse_event(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode()


def sse_data(data: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(data, separators=(',', ':'))}\n\n".encode()


def stream_headers(seq: int) -> Headers:
    return {**base_headers(seq), "cache-control": "no-cache"}


def text_pieces(text: str) -> list[str]:
    """Word-sized deltas (whitespace kept) whose concatenation is ``text``."""
    pieces = re.findall(r"\S+\s*|\s+", text)
    return pieces or [""]


def argument_pieces(arguments: str) -> list[str]:
    return [
        arguments[i : i + _ARGUMENT_PIECE] for i in range(0, len(arguments), _ARGUMENT_PIECE)
    ] or [""]


def stream_interval(step: MockStep, chunk_count: int) -> float:
    """The ``interval_s`` step parameter: seconds between consecutive chunks (default 0).

    Lets scripts model a slow stream (read timeouts, keepalives during a long queue wait).
    """
    value = step.params.get("interval_s", 0)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("'interval_s' must be a number of seconds")
    if not 0 <= value <= MAX_INTERVAL_S:
        raise ValueError("'interval_s' must be between 0 and 60")
    if value * max(chunk_count - 1, 0) > MAX_STREAM_S:
        raise ValueError("'interval_s' would make the stream longer than 600 s")
    return float(value)


def _sse_stream(
    step: MockStep, seq: int, chunks: list[bytes], outcome: dict[str, Any]
) -> SSEStream:
    interval = stream_interval(step, len(chunks))
    if interval:
        outcome["interval_s"] = interval
    return SSEStream(stream_headers(seq), chunks, outcome, interval)


def fail_after(step: MockStep) -> int:
    """``STREAM_FAIL_AFTER``'s ``n``: how many deltas are sent before the failure."""
    n = step.params.get("n", DEFAULT_FAIL_AFTER)
    if isinstance(n, bool) or not isinstance(n, int) or n < 0:
        raise ValueError("STREAM_FAIL_AFTER step needs a non-negative integer 'n'")
    return n


_STREAM_KIND = {
    MockAction.TOOL_CALL: "tool_call",
    MockAction.TRUNCATE: "truncated",
    MockAction.TRUNCATE_TOOL_CALL: "truncated_tool_call",
}


def _with_keepalives(chunks: list[bytes]) -> list[bytes]:
    """A keepalive comment before every chunk (queued requests see them before any event)."""
    out: list[bytes] = []
    for chunk in chunks:
        out.extend((KEEPALIVE, chunk))
    return out


class _ResponsesEvents:
    """Builds one Responses SSE sequence with increasing ``sequence_number`` values."""

    def __init__(self) -> None:
        self.chunks: list[bytes] = []
        self._seq = 0

    def add(self, event: str, **fields: Any) -> None:
        self.chunks.append(
            sse_event(event, {"type": event, "sequence_number": self._seq, **fields})
        )
        self._seq += 1


def _in_progress(body: dict[str, Any]) -> dict[str, Any]:
    return {
        **body,
        "status": "in_progress",
        "incomplete_details": None,
        "output": [],
        "usage": None,
    }


def _stream_message(events: _ResponsesEvents, item: dict[str, Any], pieces: list[str]) -> None:
    item_id = item["id"]
    text = "".join(pieces)
    _stream_message_partial(events, item, pieces)
    events.add(
        "response.output_text.done",
        item_id=item_id,
        output_index=0,
        content_index=0,
        text=text,
        logprobs=[],
    )
    events.add(
        "response.content_part.done",
        item_id=item_id,
        output_index=0,
        content_index=0,
        part={"type": "output_text", "text": text, "annotations": []},
    )
    events.add("response.output_item.done", output_index=0, item=item)


def _stream_function_call(
    events: _ResponsesEvents, item: dict[str, Any], *, complete: bool
) -> None:
    item_id = item["id"]
    events.add(
        "response.output_item.added",
        output_index=0,
        item={**item, "arguments": "", "status": "in_progress"},
    )
    for piece in argument_pieces(item["arguments"]):
        events.add(
            "response.function_call_arguments.delta",
            item_id=item_id,
            output_index=0,
            delta=piece,
        )
    if complete:  # a cut-off call never gets its arguments.done event
        events.add(
            "response.function_call_arguments.done",
            item_id=item_id,
            output_index=0,
            arguments=item["arguments"],
        )
    events.add("response.output_item.done", output_index=0, item=item)


def responses_stream(request_json: dict[str, Any], step: MockStep, seq: int) -> SSEStream:
    """Responses SSE for every streamable action. HTTP_ERROR and DELAY are handled by the app."""
    action = step.action
    kind = _STREAM_KIND.get(action, "text")
    _, _, final = RESPONSES_BUILDERS[kind](request_json, step, seq)
    events = _ResponsesEvents()
    events.add("response.created", response=_in_progress(final))
    events.add("response.in_progress", response=_in_progress(final))
    item = final["output"][0]
    call_ids = [item["call_id"]] if item["type"] == "function_call" else []

    if action is MockAction.STREAM_FAIL_AFTER:
        pieces = text_pieces(answer_text(step))[: fail_after(step)]
        _stream_message_partial(events, final["output"][0], pieces)
        # Documented: a stream that has already started ends with response.failed. The error
        # object's fields are [UNVERIFIED] (OpenAI shape).
        failed = {
            **final,
            "status": "failed",
            "output": [],
            "error": {"code": "server_error", "message": "The model failed mid-response."},
        }
        events.add("response.failed", response=failed)
        outcome = {"finish": "failed", "call_ids": [], "deltas": len(pieces)}
    else:
        if item["type"] == "function_call":
            _stream_function_call(events, item, complete=final["status"] == "completed")
        else:
            _stream_message(events, item, text_pieces(item["content"][0]["text"]))
        terminal = "response.completed" if final["status"] == "completed" else "response.incomplete"
        events.add(terminal, response=final)
        finish = {
            ("completed", True): "tool_call",
            ("completed", False): "completed",
            ("incomplete", True): "truncated_tool_call",
            ("incomplete", False): "truncated",
        }[(final["status"], bool(call_ids))]
        outcome = {"finish": finish, "call_ids": call_ids, "terminal_event": terminal}

    chunks = events.chunks
    if action is MockAction.KEEPALIVE_STREAM:
        chunks = _with_keepalives(chunks)
        outcome["keepalives"] = chunks.count(KEEPALIVE)
    return _sse_stream(step, seq, chunks, outcome)


def _stream_message_partial(
    events: _ResponsesEvents, item: dict[str, Any], pieces: list[str]
) -> None:
    """The opening events and the first deltas of a message that never finishes."""
    item_id = item["id"]
    events.add(
        "response.output_item.added",
        output_index=0,
        item={**item, "status": "in_progress", "content": []},
    )
    events.add(
        "response.content_part.added",
        item_id=item_id,
        output_index=0,
        content_index=0,
        part={"type": "output_text", "text": "", "annotations": []},
    )
    for piece in pieces:
        events.add(
            "response.output_text.delta",
            item_id=item_id,
            output_index=0,
            content_index=0,
            delta=piece,
            logprobs=[],
        )


def _chat_chunk(
    final: dict[str, Any],
    delta: dict[str, Any],
    finish_reason: str | None,
    extra: dict[str, Any] | None = None,
) -> bytes:
    return sse_data(
        {
            "id": final["id"],
            "object": "chat.completion.chunk",
            "created": final["created"],
            "model": final["model"],
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
            **(extra or {}),
        }
    )


GROQ_STREAM_ERROR = "The model stopped the stream early (FlightCheck mock)."


def chat_stream(
    request_json: dict[str, Any], step: MockStep, seq: int, *, groq: bool = False
) -> SSEStream:
    """Chat SSE: role chunk, content or tool-call deltas, a ``finish_reason`` chunk, ``[DONE]``.

    ``STREAM_FAIL_AFTER`` sends ``n`` content chunks and ends the body with no finish chunk and
    no ``[DONE]`` (Chat has no documented failure event).

    ``groq=True`` (Groq routes): the first and final chunks carry ``x_groq.id`` and the final
    one ``x_groq.usage`` (ST2), plus a top-level ``usage`` when the request set
    ``stream_options.include_usage`` (ST2). ``STREAM_FAIL_AFTER`` sends the ``n`` content chunks,
    then a chunk with ``x_groq.error`` and no choices, then ends without ``[DONE]``: the field
    is documented (ST3), the wire format around it is [UNVERIFIED].
    """
    action = step.action
    kind = _STREAM_KIND.get(action, "text")
    _, _, final = CHAT_BUILDERS[kind](request_json, step, seq)
    choice = final["choices"][0]
    message = choice["message"]
    tool_calls = message.get("tool_calls") or []
    chunks: list[bytes] = []
    first: dict[str, Any] | None = None
    last: dict[str, Any] | None = None
    if groq:
        rid = groq_request_id(seq)
        first = {"x_groq": {"id": rid}}
        usage = groq_usage(final["usage"])
        last = {"x_groq": {"id": rid, "usage": usage}}
        options = request_json.get("stream_options")
        if isinstance(options, dict) and options.get("include_usage") is True:
            last["usage"] = usage

    if action is MockAction.STREAM_FAIL_AFTER:
        pieces = text_pieces(answer_text(step))[: fail_after(step)]
        chunks.append(_chat_chunk(final, {"role": "assistant", "content": ""}, None, first))
        chunks.extend(_chat_chunk(final, {"content": p}, None) for p in pieces)
        outcome: dict[str, Any] = {
            "finish": "eof_without_done",
            "call_ids": [],
            "deltas": len(pieces),
            "done_sent": False,
        }
        if groq:
            chunks.append(
                sse_data(
                    {
                        "id": final["id"],
                        "object": "chat.completion.chunk",
                        "created": final["created"],
                        "model": final["model"],
                        "choices": [],
                        "x_groq": {"id": groq_request_id(seq), "error": GROQ_STREAM_ERROR},
                    }
                )
            )
            outcome["finish"] = "x_groq_error"
    else:
        if tool_calls:
            call = tool_calls[0]
            opening = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "index": 0,
                        "id": call["id"],
                        "type": "function",
                        "function": {"name": call["function"]["name"], "arguments": ""},
                    }
                ],
            }
            chunks.append(_chat_chunk(final, opening, None, first))
            for piece in argument_pieces(call["function"]["arguments"]):
                delta = {"tool_calls": [{"index": 0, "function": {"arguments": piece}}]}
                chunks.append(_chat_chunk(final, delta, None))
        else:
            chunks.append(_chat_chunk(final, {"role": "assistant", "content": ""}, None, first))
            for piece in text_pieces(message["content"]):
                chunks.append(_chat_chunk(final, {"content": piece}, None))
        chunks.append(_chat_chunk(final, {}, choice["finish_reason"], last))
        chunks.append(DONE)
        truncated = choice["finish_reason"] == "length"
        if tool_calls:
            finish = "truncated_tool_call" if truncated else "tool_call"
        else:
            finish = "truncated" if truncated else "completed"
        outcome = {
            "finish": finish,
            "call_ids": [c["id"] for c in tool_calls],
            "finish_reason": choice["finish_reason"],
            "done_sent": True,
        }

    if action is MockAction.KEEPALIVE_STREAM:
        chunks = _with_keepalives(chunks)
        outcome["keepalives"] = chunks.count(KEEPALIVE)
    return _sse_stream(step, seq, chunks, outcome)
