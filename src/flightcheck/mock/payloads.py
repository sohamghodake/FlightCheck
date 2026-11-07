"""Pure payload builders for the mock (plan §18.2). No FastAPI imports: unit-testable.

Action builders take ``(request_json, step, seq)`` and return ``(status, headers, body)``.
Shapes follow docs/BHARATCODE_CONTRACT.md §15.1 where it is specific ("OpenAI-compatible Chat
Completions or Responses"; truncation fields) and OpenAI conventions elsewhere, carrying only
the fields the OpenAI SDK models and FlightCheck's validators need. Everything beyond the
documented fields is [UNVERIFIED] for BharatCode.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from flightcheck.core.config import BHARATCODE_DEFAULT_MODEL, BHARATCODE_DOCUMENTED_MODELS
from flightcheck.mock.faults import MockStep

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
    """``HTTP_ERROR``: ``status`` (default 503, documented as "model busy"), message, code."""
    status = step.params.get("status", 503)
    if isinstance(status, bool) or not isinstance(status, int) or not 400 <= status <= 599:
        raise ValueError("HTTP_ERROR step needs an integer status between 400 and 599")
    message = step.params.get("message")
    if not isinstance(message, str):
        message = "Model is busy, retry in a few seconds." if status == 503 else "Mock error."
    code = step.params.get("code")
    headers = base_headers(seq)
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
