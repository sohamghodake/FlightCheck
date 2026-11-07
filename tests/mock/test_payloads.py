"""Payload builders: shapes, deterministic IDs, argument generation, SDK parseability."""

import json

import pytest
from openai.types import Model
from openai.types.chat import ChatCompletion
from openai.types.responses import Response

from flightcheck.core.config import BHARATCODE_DEFAULT_MODEL
from flightcheck.mock import payloads
from flightcheck.mock.faults import MockAction, MockStep

RESPONSES_KEYS = {
    "id",
    "object",
    "created_at",
    "model",
    "status",
    "output",
    "incomplete_details",
    "usage",
}
CHAT_KEYS = {"id", "object", "created", "model", "choices", "usage"}

SCHEMA = {
    "type": "object",
    "properties": {
        "city": {"type": "string"},
        "days": {"type": "integer"},
        "temp": {"type": "number"},
        "metric": {"type": "boolean"},
        "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
        "note": {"type": "string"},
    },
    "required": ["city", "days", "temp", "metric", "unit"],
    "additionalProperties": False,
}
RESPONSES_TOOL = {"type": "function", "name": "get_weather", "parameters": SCHEMA, "strict": True}
CHAT_TOOL = {"type": "function", "function": {"name": "get_weather", "parameters": SCHEMA}}
EXPECTED_ARGS = {"city": "fc_value", "days": 1, "temp": 1, "metric": True, "unit": "celsius"}


def step(action: MockAction, **params: object) -> MockStep:
    return MockStep(id="s", action=action, params={"scenario": "S01", **params})


def req(**extra: object) -> dict:
    return {"model": "qwen-3.8-27b", "input": "hi", "store": False, **extra}


# --- argument generation --------------------------------------------------------------------


def test_arguments_cover_required_primitives_and_enum() -> None:
    assert payloads.arguments_for_schema(SCHEMA) == EXPECTED_ARGS  # "note" is optional


def test_arguments_edge_cases() -> None:
    assert payloads.arguments_for_schema({}) == {}
    assert payloads.arguments_for_schema(None) == {}
    assert payloads.arguments_for_schema({"type": "object", "properties": {"a": {}}}) == {}
    nested = {
        "type": "object",
        "properties": {
            "loc": {
                "type": "object",
                "properties": {"lat": {"type": "number"}, "x": {"type": "string"}},
                "required": ["lat"],
            },
            "tags": {"type": "array", "items": {"type": "string"}},
            "maybe": {"type": ["string", "null"]},
            "fixed": {"const": "v1"},
            "ghost": {"type": "string"},
        },
        "required": ["loc", "tags", "maybe", "fixed", "missing"],
    }
    assert payloads.arguments_for_schema(nested) == {
        "loc": {"lat": 1},
        "tags": [],
        "maybe": "fc_value",
        "fixed": "v1",
    }


def test_find_function_tool_both_styles() -> None:
    assert payloads.find_function_tool({"tools": [RESPONSES_TOOL]}) == (
        "get_weather",
        SCHEMA,
        "responses",
    )
    assert payloads.find_function_tool({"tools": [CHAT_TOOL]}) == ("get_weather", SCHEMA, "chat")
    assert payloads.find_function_tool({}) is None
    assert payloads.find_function_tool({"tools": []}) is None
    assert payloads.find_function_tool({"tools": [{"type": "web_search"}, "x"]}) is None
    first = {"tools": [{"type": "web_search"}, {"type": "function", "name": "b"}]}
    assert payloads.find_function_tool(first) == ("b", {}, "responses")


def test_cut_arguments_is_never_valid_json() -> None:
    full = json.dumps(EXPECTED_ARGS)
    for text in (full, "{}", '{"a":1}'):
        cut = payloads.cut_arguments(text)
        assert text.startswith(cut) and len(cut) < len(text)
        with pytest.raises(json.JSONDecodeError):
            json.loads(cut)


# --- Responses ------------------------------------------------------------------------------


def test_responses_text() -> None:
    status, headers, body = payloads.responses_text(req(), step(MockAction.OK_TEXT), 3)
    assert status == 200 and headers == {"x-request-id": "fc_mock_3"}
    assert RESPONSES_KEYS <= body.keys()
    assert body["object"] == "response" and body["status"] == "completed"
    assert body["incomplete_details"] is None
    assert body["id"] == "resp_fc_mock_3" and body["model"] == "qwen-3.8-27b"
    parsed = Response.model_validate(body)
    assert parsed.output_text == "FlightCheck mock answer [S01]"
    assert parsed.usage is not None and parsed.usage.total_tokens > 0


def test_responses_text_custom_text_and_default_model() -> None:
    _, _, body = payloads.responses_text({"input": "x"}, step(MockAction.OK_TEXT, text="hey"), 1)
    assert body["model"] == BHARATCODE_DEFAULT_MODEL
    assert Response.model_validate(body).output_text == "hey"


@pytest.mark.parametrize("tool", [RESPONSES_TOOL, CHAT_TOOL])
def test_responses_tool_call(tool: dict) -> None:
    status, _, body = payloads.responses_tool_call(req(tools=[tool]), step(MockAction.TOOL_CALL), 2)
    assert status == 200 and body["status"] == "completed"
    parsed = Response.model_validate(body)
    (item,) = parsed.output
    assert item.type == "function_call"
    assert item.call_id == "call_fc_S01_2" and item.name == "get_weather"
    assert json.loads(item.arguments) == EXPECTED_ARGS


def test_responses_tool_call_without_tools_falls_back_to_text() -> None:
    _, _, body = payloads.responses_tool_call(req(), step(MockAction.TOOL_CALL), 1)
    assert Response.model_validate(body).output_text == "FlightCheck mock answer [S01]"


def test_responses_truncated() -> None:
    status, _, body = payloads.responses_truncated(req(), step(MockAction.TRUNCATE), 4)
    assert status == 200
    assert body["status"] == "incomplete"
    assert body["incomplete_details"] == {"reason": "max_output_tokens"}
    parsed = Response.model_validate(body)
    assert parsed.output_text == "FlightCheck partial [S01]"
    assert parsed.incomplete_details is not None
    assert parsed.incomplete_details.reason == "max_output_tokens"


def test_responses_truncated_tool_call() -> None:
    _, _, body = payloads.responses_truncated_tool_call(
        req(tools=[RESPONSES_TOOL]), step(MockAction.TRUNCATE_TOOL_CALL), 5
    )
    assert body["status"] == "incomplete"
    assert body["incomplete_details"] == {"reason": "max_output_tokens"}
    (item,) = Response.model_validate(body).output
    assert item.type == "function_call" and item.call_id == "call_fc_S01_5"
    assert item.status == "incomplete"
    with pytest.raises(json.JSONDecodeError):
        json.loads(item.arguments)


def test_responses_truncated_tool_call_without_tools_is_truncated_text() -> None:
    _, _, body = payloads.responses_truncated_tool_call(
        req(), step(MockAction.TRUNCATE_TOOL_CALL), 1
    )
    assert body["status"] == "incomplete"
    assert Response.model_validate(body).output_text == "FlightCheck partial [S01]"


# --- Chat -----------------------------------------------------------------------------------


def chat_req(**extra: object) -> dict:
    return {"model": "qwen-3.8-27b", "messages": [{"role": "user", "content": "hi"}], **extra}


def test_chat_text() -> None:
    status, headers, body = payloads.chat_text(chat_req(), step(MockAction.OK_TEXT), 7)
    assert status == 200 and headers["x-request-id"] == "fc_mock_7"
    assert CHAT_KEYS <= body.keys() and body["object"] == "chat.completion"
    parsed = ChatCompletion.model_validate(body)
    assert parsed.choices[0].message.content == "FlightCheck mock answer [S01]"
    assert parsed.choices[0].finish_reason == "stop"


@pytest.mark.parametrize("tool", [CHAT_TOOL, RESPONSES_TOOL])
def test_chat_tool_call(tool: dict) -> None:
    _, _, body = payloads.chat_tool_call(chat_req(tools=[tool]), step(MockAction.TOOL_CALL), 2)
    choice = ChatCompletion.model_validate(body).choices[0]
    assert choice.finish_reason == "tool_calls"
    assert choice.message.content is None
    (call,) = choice.message.tool_calls or []
    assert call.id == "call_fc_S01_2" and call.type == "function"
    assert call.function.name == "get_weather"
    assert json.loads(call.function.arguments) == EXPECTED_ARGS


def test_chat_tool_call_without_tools_falls_back_to_text() -> None:
    _, _, body = payloads.chat_tool_call(chat_req(), step(MockAction.TOOL_CALL), 1)
    choice = ChatCompletion.model_validate(body).choices[0]
    assert choice.message.content == "FlightCheck mock answer [S01]"
    assert choice.message.tool_calls is None


def test_chat_truncated() -> None:
    _, _, body = payloads.chat_truncated(chat_req(), step(MockAction.TRUNCATE), 1)
    choice = ChatCompletion.model_validate(body).choices[0]
    assert choice.finish_reason == "length"
    assert choice.message.content == "FlightCheck partial [S01]"


def test_chat_truncated_tool_call() -> None:
    _, _, body = payloads.chat_truncated_tool_call(
        chat_req(tools=[CHAT_TOOL]), step(MockAction.TRUNCATE_TOOL_CALL), 9
    )
    choice = ChatCompletion.model_validate(body).choices[0]
    assert choice.finish_reason == "length"
    (call,) = choice.message.tool_calls or []
    assert call.id == "call_fc_S01_9"
    with pytest.raises(json.JSONDecodeError):
        json.loads(call.function.arguments)
    _, _, plain = payloads.chat_truncated_tool_call(chat_req(), step(MockAction.TRUNCATE), 1)
    assert plain["choices"][0]["finish_reason"] == "length"
    assert "tool_calls" not in plain["choices"][0]["message"]


def test_builders_are_deterministic() -> None:
    for builder in (*payloads.RESPONSES_BUILDERS.values(), *payloads.CHAT_BUILDERS.values()):
        a = builder(req(tools=[RESPONSES_TOOL]), step(MockAction.OK_TEXT), 1)
        b = builder(req(tools=[RESPONSES_TOOL]), step(MockAction.OK_TEXT), 1)
        assert a == b


def test_default_scenario_in_markers_and_ids() -> None:
    plain = MockStep(id="s", action=MockAction.TOOL_CALL)
    _, _, body = payloads.chat_tool_call(chat_req(tools=[CHAT_TOOL]), plain, 4)
    assert body["choices"][0]["message"]["tool_calls"][0]["id"] == "call_fc_mock_4"
    _, _, text = payloads.chat_text(chat_req(), plain, 4)
    assert text["choices"][0]["message"]["content"] == "FlightCheck mock answer [mock]"


# --- errors and models ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (400, "invalid_request_error"),
        (401, "authentication_error"),
        (429, "rate_limit_error"),
        (500, "server_error"),
        (503, "server_error"),
        (418, "invalid_request_error"),
    ],
)
def test_error_body(status: int, kind: str) -> None:
    assert payloads.error_body(status, "msg", "c") == {
        "error": {"message": "msg", "type": kind, "code": "c"}
    }


def test_http_error_defaults_to_documented_503() -> None:
    status, headers, body = payloads.http_error(req(), step(MockAction.HTTP_ERROR), 2)
    assert status == 503 and headers == {"x-request-id": "fc_mock_2"}
    assert body["error"]["type"] == "server_error"
    assert "retry" in body["error"]["message"].lower()


def test_http_error_params() -> None:
    status, headers, body = payloads.http_error(
        req(),
        step(MockAction.HTTP_ERROR, status=429, message="slow down", code="rate", retry_after=2),
        1,
    )
    assert status == 429 and headers["retry-after"] == "2"
    assert body == {"error": {"message": "slow down", "type": "rate_limit_error", "code": "rate"}}


@pytest.mark.parametrize("bad", [200, 600, "503", True, None])
def test_http_error_rejects_bad_status(bad: object) -> None:
    with pytest.raises(ValueError):
        payloads.http_error(req(), step(MockAction.HTTP_ERROR, status=bad), 1)


def test_stream_failure_is_500() -> None:
    status, headers, body = payloads.stream_failure(req(), step(MockAction.STREAM_FAIL_AFTER), 1)
    assert status == 500 and headers["x-request-id"] == "fc_mock_1"
    assert body["error"]["type"] == "server_error"


def test_models_list_has_only_documented_models() -> None:
    body = payloads.models_list()
    assert body["object"] == "list"
    assert [m["id"] for m in body["data"]] == ["deepseek-v4.1-flash", "qwen-3.8-27b"]
    for item in body["data"]:
        Model.model_validate(item)
