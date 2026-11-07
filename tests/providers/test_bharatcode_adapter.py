"""BharatCodeAdapter: payloads, pre-flight, normalisation, streams, raw_post, registry."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx2
import pytest
from provider_bridge import (
    FAKE_KEY,
    Sleeps,
    make_adapter,
    make_config,
    mock_transport,
    raw_transport,
    steps,
)

from flightcheck.core.capabilities import Capability
from flightcheck.core.config import ProviderConfig
from flightcheck.core.errors import ConfigError, ErrorKind, NetworkDisabledError, ProviderError
from flightcheck.core.models import Endpoint
from flightcheck.mock.faults import MockAction as A
from flightcheck.providers.base import (
    ChatRequest,
    FinishState,
    NormalizedResponse,
    ResponsesRequest,
    ToolCall,
    chat_finish_state,
    responses_finish_state,
)
from flightcheck.providers.bharatcode import BharatCodeAdapter
from flightcheck.providers.registry import build_adapter

MODEL = "qwen-3.8-27b"
SCHEMA = {
    "type": "object",
    "properties": {"city": {"type": "string"}},
    "required": ["city"],
    "additionalProperties": False,
}
R_TOOL = {"type": "function", "name": "get_weather", "parameters": SCHEMA, "strict": True}
C_TOOL = {"type": "function", "function": {"name": "get_weather", "parameters": SCHEMA}}
REQ = ResponsesRequest(model=MODEL, input="hi")
CHAT = ChatRequest(model=MODEL, messages=[{"role": "user", "content": "hi"}])


def adapter_for(*script: Any) -> tuple[BharatCodeAdapter, Any]:
    bridge = mock_transport(steps(*script))
    adapter, _ = make_adapter(bridge.transport)
    return adapter, bridge


def offline_adapter() -> tuple[BharatCodeAdapter, list[httpx2.Request]]:
    transport, seen = raw_transport()
    adapter, _ = make_adapter(transport)
    return adapter, seen


# --- Payload builders -----------------------------------------------------------------------


def test_responses_payload_always_sends_store_false() -> None:
    adapter, _ = offline_adapter()
    assert adapter.responses_payload(REQ)["store"] is False
    sneaky = REQ.model_copy(update={"extra_body": {"store": True, "model": "other"}})
    payload = adapter.responses_payload(sneaky)
    assert payload["store"] is False and payload["model"] == MODEL  # typed fields win


def test_responses_payload_mapping() -> None:
    adapter, _ = offline_adapter()
    fmt = {"type": "json_schema", "name": "x", "schema": SCHEMA, "strict": True}
    req = ResponsesRequest(
        model=MODEL,
        input=[{"role": "user", "content": "hi"}],
        instructions="be brief",
        tools=[R_TOOL],
        tool_choice={"type": "function", "name": "get_weather"},
        text_format=fmt,
        max_output_tokens=4000,
        reasoning_effort="none",
        extra_body={"text": {"verbosity": "low"}, "stream": True, "metadata": {"a": "b"}},
    )
    assert adapter.responses_payload(req) == {
        "model": MODEL,
        "input": [{"role": "user", "content": "hi"}],
        "instructions": "be brief",
        "tools": [R_TOOL],
        "tool_choice": {"type": "function", "name": "get_weather"},
        "text": {"verbosity": "low", "format": fmt},
        "max_output_tokens": 4000,
        "reasoning": {"effort": "none"},
        "store": False,
        "metadata": {"a": "b"},
    }
    assert adapter.responses_payload(req, stream=True)["stream"] is True


def test_payloads_are_copies() -> None:
    adapter, _ = offline_adapter()
    req = REQ.model_copy(update={"tools": [dict(R_TOOL)], "input": [{"role": "user"}]})
    payload = adapter.responses_payload(req)
    payload["tools"][0]["name"] = "changed"
    payload["input"][0]["role"] = "changed"
    assert req.tools[0]["name"] == "get_weather" and req.input[0]["role"] == "user"


def test_chat_payload_mapping() -> None:
    adapter, _ = offline_adapter()
    req = ChatRequest(
        model=MODEL,
        messages=[{"role": "user", "content": "hi"}],
        tools=[C_TOOL],
        tool_choice="auto",
        max_tokens=100,
        reasoning_effort="none",
        extra_body={"stream": True, "temperature": 0},
    )
    assert adapter.chat_payload(req) == {
        "model": MODEL,
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [C_TOOL],
        "tool_choice": "auto",
        "max_tokens": 100,
        "reasoning_effort": "none",
        "temperature": 0,
    }
    assert "store" not in adapter.chat_payload(req)
    assert adapter.chat_payload(req, stream=True)["stream"] is True


@pytest.mark.parametrize(("value", "sent"), [(32_000, 32_000), (32_001, 32_000), (50_000, 32_000)])
def test_output_budget_is_clamped_with_a_warning(
    value: int, sent: int, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="flightcheck")
    adapter, _ = offline_adapter()
    assert (
        adapter.responses_payload(REQ.model_copy(update={"max_output_tokens": value}))[
            "max_output_tokens"
        ]
        == sent
    )
    assert adapter.chat_payload(CHAT.model_copy(update={"max_tokens": value}))["max_tokens"] == sent
    warnings = [r for r in caplog.records if "clamped to 32000" in r.getMessage()]
    assert len(warnings) == (2 if value > 32_000 else 0)
    assert all(r.levelno == logging.WARNING for r in warnings)


def test_mock_receives_the_clamped_store_false_payload() -> None:
    adapter, bridge = adapter_for(A.OK_TEXT, A.OK_TEXT)
    adapter.create_response(REQ.model_copy(update={"max_output_tokens": 99_999}))
    adapter.create_chat_completion(CHAT.model_copy(update={"max_tokens": 40_000}))
    responses, chat = sorted(bridge.recorder.list(), key=lambda x: x.seq)
    assert responses.request_json["store"] is False
    assert responses.request_json["max_output_tokens"] == 32_000
    assert chat.request_json["max_tokens"] == 32_000
    assert responses.response_summary["auth"] == "fake_key"
    assert responses.path == "/api/model/v1/responses"
    assert chat.path == "/api/model/v1/chat/completions"


# --- Pre-flight (plan §14.7) ----------------------------------------------------------------

REJECTED = [
    ({"store": True}, "store=true"),
    ({"previous_response_id": "resp_1"}, "previous_response_id"),
    ({"tools": [{"type": "web_search"}]}, "Hosted tools"),
    ({"tools": [R_TOOL, {"type": "file_search"}]}, "Hosted tools"),
    ({"background": True}, "background=true"),
    (
        {"input": [{"role": "user", "content": [{"type": "input_audio", "audio": "x"}]}]},
        "input_audio",
    ),
    (
        {"input": [{"role": "user", "content": [{"type": "input_file", "file_id": "f"}]}]},
        "input_file",
    ),
    (
        {"input": [{"role": "user", "content": [{"type": "input_text", "file_data": "x"}]}]},
        "file_data",
    ),
    ({"max_output_tokens": 32_001}, "max_output_tokens=32001"),
]
ACCEPTED = [
    {"store": False},
    {},
    {"previous_response_id": None},
    {"tools": [R_TOOL]},
    {"tools": [C_TOOL]},
    {"background": False},
    {"input": [{"role": "user", "content": [{"type": "input_text", "text": "x"}]}]},
    {
        "input": [
            {"role": "user", "content": [{"type": "input_image", "image_url": "https://x/i.png"}]}
        ]
    },
    {"max_output_tokens": 32_000},
]


def base_payload(**extra: Any) -> dict[str, Any]:
    return {"model": MODEL, "input": "hi", "store": False, **extra}


@pytest.mark.parametrize(("extra", "needle"), REJECTED)
def test_preflight_rejects_documented_unsupported_features(extra: dict, needle: str) -> None:
    adapter, _ = offline_adapter()
    payload = base_payload()
    payload.update(extra)
    problems = adapter.preflight(payload, Endpoint.RESPONSES)
    assert len(problems) == 1 and needle in problems[0]


@pytest.mark.parametrize("extra", ACCEPTED)
def test_preflight_accepts_look_alikes(extra: dict) -> None:
    adapter, _ = offline_adapter()
    payload = base_payload()
    payload.update(extra)
    assert adapter.preflight(payload, Endpoint.RESPONSES) == []


def test_preflight_reports_every_violation() -> None:
    adapter, _ = offline_adapter()
    payload = base_payload(store=True, background=True, previous_response_id="r")
    assert len(adapter.preflight(payload, Endpoint.RESPONSES)) == 3


def test_preflight_on_chat_checks_only_model_and_budget() -> None:
    adapter, _ = offline_adapter()
    chat = {"model": MODEL, "messages": [], "store": True, "tools": [{"type": "web_search"}]}
    assert adapter.preflight(chat, Endpoint.CHAT_COMPLETIONS) == []  # no documented chat 400s
    assert adapter.preflight({**chat, "max_tokens": 32_001}, Endpoint.CHAT_COMPLETIONS)
    assert adapter.preflight({**chat, "max_tokens": 32_000}, Endpoint.CHAT_COMPLETIONS) == []


@pytest.mark.parametrize("model", [None, "", "  ", 5])
def test_preflight_requires_a_model(model: Any) -> None:
    adapter, _ = offline_adapter()
    problems = adapter.preflight({"model": model, "input": "x"}, Endpoint.RESPONSES)
    assert problems == ["model is required on every request."]


@pytest.mark.parametrize(
    ("update", "needle"),
    [
        ({"store": True}, "store=true"),
        ({"extra_body": {"background": True}}, "background"),
        ({"tools": [{"type": "web_search"}]}, "Hosted"),
        ({"extra_body": {"previous_response_id": "r"}}, "previous"),
    ],
)
def test_operations_refuse_before_any_network_call(update: dict, needle: str) -> None:
    adapter, seen = offline_adapter()
    req = REQ.model_copy(update=update)
    for call in (adapter.create_response, adapter.stream_response):
        with pytest.raises(ProviderError) as info:
            call(req)  # stream_response refuses eagerly, before iteration
        assert info.value.kind is ErrorKind.UNSUPPORTED_CAPABILITY
        assert info.value.http_status is None and needle in info.value.message
        assert "refused locally" in info.value.message
    assert seen == []


def test_bad_request_kind_for_budget_and_model_problems() -> None:
    adapter, seen = offline_adapter()
    with pytest.raises(ProviderError) as info:
        adapter.raw_post("responses", {"model": MODEL, "input": "x", "max_output_tokens": 40_000})
    assert info.value.kind is ErrorKind.BAD_REQUEST
    with pytest.raises(ProviderError) as info:
        adapter.create_chat_completion(CHAT.model_copy(update={"model": ""}))
    assert info.value.kind is ErrorKind.BAD_REQUEST
    assert seen == []


def test_unknown_model_warns_once_and_documented_models_do_not(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="flightcheck")
    adapter, bridge = adapter_for(A.OK_TEXT, A.OK_TEXT, A.OK_TEXT, A.OK_TEXT)
    adapter.create_response(REQ)
    assert not caplog.records
    adapter.create_response(REQ.model_copy(update={"model": "gpt-5"}))
    adapter.create_response(REQ.model_copy(update={"model": "gpt-5"}))
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "documented catalogue" in warnings[0].getMessage()
    adapter.create_response(REQ.model_copy(update={"model": "deepseek-v4.1-flash"}))
    assert any("Pro only" in r.getMessage() for r in caplog.records if r.levelno == logging.INFO)
    assert len(bridge.requests) == 4  # a warning never blocks the request


def test_declared_unsupported_capability_is_refused() -> None:
    transport, seen = raw_transport()
    config = make_config(declared_capabilities={Capability.STREAM_CHAT: False})
    adapter = BharatCodeAdapter(config, transport=transport, sleep=Sleeps())
    with pytest.raises(ProviderError) as info:
        adapter.stream_chat_completion(CHAT)
    assert info.value.kind is ErrorKind.UNSUPPORTED_CAPABILITY
    assert "stream_chat is marked declared_unsupported" in info.value.message
    assert seen == []


# --- Finish states (plan §14.3) -------------------------------------------------------------


@pytest.mark.parametrize(
    ("finish_reason", "has_calls", "state"),
    [
        ("stop", False, FinishState.COMPLETE),
        ("stop", True, FinishState.TOOL_CALLS),
        ("tool_calls", True, FinishState.TOOL_CALLS),
        ("tool_calls", False, FinishState.UNKNOWN),
        ("length", False, FinishState.TRUNCATED),
        ("length", True, FinishState.TRUNCATED),
        ("content_filter", False, FinishState.UNKNOWN),
        ("function_call", True, FinishState.UNKNOWN),
        (None, False, FinishState.UNKNOWN),
        (5, False, FinishState.UNKNOWN),
    ],
)
def test_chat_finish_state_table(finish_reason: Any, has_calls: bool, state: FinishState) -> None:
    assert chat_finish_state(finish_reason, has_calls) is state


@pytest.mark.parametrize(
    ("status", "has_calls", "state"),
    [
        ("completed", False, FinishState.COMPLETE),
        ("completed", True, FinishState.TOOL_CALLS),
        ("incomplete", False, FinishState.TRUNCATED),
        ("incomplete", True, FinishState.TRUNCATED),
        ("failed", False, FinishState.FAILED),
        ("in_progress", False, FinishState.UNKNOWN),
        ("cancelled", True, FinishState.UNKNOWN),
        ("queued", False, FinishState.UNKNOWN),
        (None, False, FinishState.UNKNOWN),
    ],
)
def test_responses_finish_state_table(status: Any, has_calls: bool, state: FinishState) -> None:
    assert responses_finish_state(status, has_calls) is state


def normalized(state: FinishState, calls: list[ToolCall]) -> NormalizedResponse:
    return NormalizedResponse(
        provider="bharatcode",
        endpoint=Endpoint.RESPONSES,
        model=MODEL,
        text="",
        tool_calls=calls,
        finish_state=state,
        finish_detail=None,
        http_status=200,
        request_id=None,
        raw={},
    )


@pytest.mark.parametrize(
    "state",
    [FinishState.TRUNCATED, FinishState.FAILED, FinishState.UNKNOWN, FinishState.COMPLETE],
)
def test_executable_tool_calls_is_empty_unless_tool_calls(state: FinishState) -> None:
    calls = [ToolCall(call_id="c", name="f", arguments_raw="{}", complete=True)]
    result = normalized(state, calls)
    assert result.executable_tool_calls() == []
    assert result.is_complete is (state is FinishState.COMPLETE)


def test_executable_tool_calls_keeps_only_complete_calls() -> None:
    done = ToolCall(call_id="a", name="f", arguments_raw="{}", complete=True)
    cut = ToolCall(call_id="b", name="f", arguments_raw='{"x', complete=False)
    assert normalized(FinishState.TOOL_CALLS, [done, cut]).executable_tool_calls() == [done]


# --- Adapter ↔ mock (Responses and Chat × text / tool / truncated) --------------------------


def test_responses_text() -> None:
    adapter, _ = adapter_for(A.OK_TEXT)
    result = adapter.create_response(REQ)
    assert result.finish_state is FinishState.COMPLETE and result.is_complete
    assert result.text == "FlightCheck mock answer [mock]"
    assert result.finish_detail == "completed"
    assert result.endpoint is Endpoint.RESPONSES and result.provider == "bharatcode"
    assert result.model == MODEL and result.http_status == 200
    assert result.usage is not None and result.usage.output_tokens and result.usage.input_tokens
    assert result.usage.reasoning_tokens == 0
    assert result.raw["id"] == "resp_fc_mock_1"
    assert result.unverified_capabilities == []


def test_responses_tool_call() -> None:
    adapter, _ = adapter_for(A.TOOL_CALL)
    result = adapter.create_response(REQ.model_copy(update={"tools": [R_TOOL]}))
    assert result.finish_state is FinishState.TOOL_CALLS
    [call] = result.executable_tool_calls()
    assert call.name == "get_weather" and call.complete
    assert call.call_id == "call_fc_mock_1"
    assert json.loads(call.arguments_raw) == {"city": "fc_value"}


def test_responses_truncated() -> None:
    adapter, _ = adapter_for(A.TRUNCATE)
    result = adapter.create_response(REQ)
    assert result.finish_state is FinishState.TRUNCATED and not result.is_complete
    assert result.finish_detail == "max_output_tokens"
    assert result.text  # partial output is preserved


def test_responses_truncated_tool_call_is_never_executable() -> None:
    adapter, _ = adapter_for(A.TRUNCATE_TOOL_CALL)
    result = adapter.create_response(REQ.model_copy(update={"tools": [R_TOOL]}))
    assert result.finish_state is FinishState.TRUNCATED
    [call] = result.tool_calls
    assert call.complete is False
    with pytest.raises(ValueError):
        json.loads(call.arguments_raw)  # kept exactly as received, never repaired
    assert result.executable_tool_calls() == []


def test_chat_text() -> None:
    adapter, _ = adapter_for(A.OK_TEXT)
    result = adapter.create_chat_completion(CHAT)
    assert result.finish_state is FinishState.COMPLETE
    assert result.finish_detail == "stop" and result.endpoint is Endpoint.CHAT_COMPLETIONS
    assert result.text == "FlightCheck mock answer [mock]"
    assert result.usage is not None and result.usage.input_tokens == 12
    assert result.usage.reasoning_tokens is None


@pytest.mark.parametrize("tool", [C_TOOL, R_TOOL])
def test_chat_tool_call(tool: dict) -> None:
    adapter, _ = adapter_for(A.TOOL_CALL)
    result = adapter.create_chat_completion(CHAT.model_copy(update={"tools": [tool]}))
    assert result.finish_state is FinishState.TOOL_CALLS and result.text == ""
    [call] = result.executable_tool_calls()
    assert call.name == "get_weather" and json.loads(call.arguments_raw) == {"city": "fc_value"}


def test_chat_truncated_and_truncated_tool_call() -> None:
    adapter, _ = adapter_for(A.TRUNCATE, A.TRUNCATE_TOOL_CALL)
    truncated = adapter.create_chat_completion(CHAT)
    assert truncated.finish_state is FinishState.TRUNCATED and truncated.finish_detail == "length"
    cut = adapter.create_chat_completion(CHAT.model_copy(update={"tools": [C_TOOL]}))
    assert cut.finish_state is FinishState.TRUNCATED
    assert [c.complete for c in cut.tool_calls] == [False] and cut.executable_tool_calls() == []


def test_non_stream_fail_after_is_a_server_error() -> None:
    adapter, _ = adapter_for(A.STREAM_FAIL_AFTER)
    with pytest.raises(ProviderError) as info:
        adapter.create_response(REQ)
    assert info.value.kind is ErrorKind.SERVER and info.value.http_status == 500


def test_list_models() -> None:
    adapter, _ = adapter_for()
    assert [m.id for m in adapter.list_models()] == ["deepseek-v4.1-flash", "qwen-3.8-27b"]


def body_response(body: dict) -> NormalizedResponse:
    transport, _ = raw_transport(body=json.dumps(body).encode())
    adapter, _ = make_adapter(transport)
    return adapter.create_response(REQ)


def test_empty_output_is_complete_with_empty_text() -> None:
    result = body_response({"status": "completed", "output": []})
    assert result.finish_state is FinishState.COMPLETE and result.text == ""
    assert result.tool_calls == [] and result.usage is None
    assert result.model == MODEL  # falls back to the requested model


def test_missing_status_is_unknown_and_never_complete() -> None:
    call = {"type": "function_call", "call_id": "c", "name": "f", "arguments": "{}"}
    result = body_response({"output": [call]})
    assert result.finish_state is FinishState.UNKNOWN and result.finish_detail is None
    assert result.tool_calls[0].complete is False and result.executable_tool_calls() == []


def test_incomplete_item_inside_completed_response_is_not_executable() -> None:
    done = {"type": "function_call", "call_id": "a", "name": "f", "arguments": "{}"}
    cut = {**done, "call_id": "b", "status": "incomplete"}
    result = body_response({"status": "completed", "output": [done, cut]})
    assert [c.call_id for c in result.executable_tool_calls()] == ["a"]


def test_unknown_output_items_are_kept_in_raw_only() -> None:
    items = [
        {"type": "reasoning", "summary": []},
        {"type": "message", "content": [{"type": "refusal", "refusal": "no"}]},
        {
            "type": "message",
            "content": [{"type": "output_text", "text": "A"}, {"type": "output_text", "text": "B"}],
        },
    ]
    result = body_response({"status": "completed", "output": items, "extra": 1})
    assert result.text == "AB" and result.raw["extra"] == 1


# --- Streams --------------------------------------------------------------------------------


def kinds(events: list) -> list[str]:
    return [e.kind for e in events]


def collect(stream) -> tuple[list, ProviderError | None]:  # noqa: ANN001
    events: list = []
    try:
        for event in stream:
            events.append(event)
    except ProviderError as error:
        return events, error
    return events, None


def test_responses_stream_text() -> None:
    adapter, _ = adapter_for(A.OK_TEXT)
    events, error = collect(adapter.stream_response(REQ))
    assert error is None and events[-1].kind == "completed"
    assert "".join(e.text for e in events) == "FlightCheck mock answer [mock]"
    assert events[-1].data["response"]["status"] == "completed"


def test_responses_stream_tool_call_and_truncated() -> None:
    adapter, _ = adapter_for(A.TOOL_CALL, A.TRUNCATE)
    events, error = collect(adapter.stream_response(REQ.model_copy(update={"tools": [R_TOOL]})))
    assert error is None and events[-1].kind == "completed"
    assert "tool_call_delta" in kinds(events)
    events, error = collect(adapter.stream_response(REQ))
    assert error is None and events[-1].kind == "incomplete"
    assert "".join(e.text for e in events)  # partial text kept


def test_responses_stream_failed_is_yielded_then_raised() -> None:
    adapter, bridge = adapter_for((A.STREAM_FAIL_AFTER, {"n": 2}))
    events, error = collect(adapter.stream_response(REQ))
    assert kinds(events)[-1] == "failed" and events[-1].raw_event == "response.failed"
    assert "".join(e.text for e in events) == "FlightCheck mock "
    assert error is not None and error.kind is ErrorKind.STREAM_FAILED
    assert error.http_status == 200 and error.request_id == "fc_mock_1"
    assert "The model failed mid-response. [server_error]" in error.message
    assert bridge.recorder.list()[0].response_summary["finish"] == "failed"


def test_responses_keepalive_stream() -> None:
    adapter, _ = adapter_for(A.KEEPALIVE_STREAM)
    events, error = collect(adapter.stream_response(REQ))
    assert error is None and events[0].kind == "keepalive" and events[-1].kind == "completed"
    assert "keepalive" not in "".join(e.text for e in events)


def test_chat_stream_text_tool_truncated() -> None:
    adapter, _ = adapter_for(A.OK_TEXT, A.TOOL_CALL, A.TRUNCATE)
    events, error = collect(adapter.stream_chat_completion(CHAT))
    assert error is None and events[-1].kind == "completed"
    assert "".join(e.text for e in events) == "FlightCheck mock answer [mock]"
    events, error = collect(
        adapter.stream_chat_completion(CHAT.model_copy(update={"tools": [C_TOOL]}))
    )
    assert error is None and events[-1].kind == "completed" and "tool_call_delta" in kinds(events)
    events, error = collect(adapter.stream_chat_completion(CHAT))
    assert error is None and events[-1].kind == "incomplete"


def test_chat_stream_fail_after_is_eof_failure() -> None:
    adapter, _ = adapter_for(A.STREAM_FAIL_AFTER)
    events, error = collect(adapter.stream_chat_completion(CHAT))
    assert events[-1].kind == "failed" and events[-1].data == {
        "reason": "eof_without_terminal_event"
    }
    assert error is not None and error.kind is ErrorKind.STREAM_FAILED
    assert "eof_without_terminal_event" in error.message and error.request_id == "fc_mock_1"


@pytest.mark.parametrize("call", ["responses", "chat"])
def test_keepalives_only_then_eof(call: str) -> None:
    transport, _ = raw_transport(
        body=b": keepalive\n\n" * 4, headers={"content-type": "text/event-stream"}
    )
    adapter, sleeps = make_adapter(transport)
    stream = (
        adapter.stream_response(REQ)
        if call == "responses"
        else adapter.stream_chat_completion(CHAT)
    )
    events, error = collect(stream)
    assert kinds(events) == ["keepalive"] * 4 + ["failed"]
    assert error is not None and error.kind is ErrorKind.STREAM_FAILED
    assert "ended without a terminal event" in error.message and sleeps == []


def test_chat_done_without_finish_reason_raises() -> None:
    body = b'data: {"choices": [{"index": 0, "delta": {"content": "x"}}]}\n\ndata: [DONE]\n\n'
    transport, _ = raw_transport(body=body, headers={"content-type": "text/event-stream"})
    adapter, _ = make_adapter(transport)
    events, error = collect(adapter.stream_chat_completion(CHAT))
    assert kinds(events) == ["text_delta", "failed"]
    assert error is not None and "[DONE] without any finish_reason" in error.message


def test_chat_stream_is_tagged_unverified() -> None:
    adapter, _ = offline_adapter()
    payload = adapter.chat_payload(CHAT, stream=True)
    assert adapter.unverified_capabilities(payload, Endpoint.CHAT_COMPLETIONS) == [
        Capability.STREAM_CHAT
    ]
    payload = adapter.chat_payload(
        CHAT.model_copy(update={"extra_body": {"response_format": {"type": "json_object"}}})
    )
    assert adapter.unverified_capabilities(payload, Endpoint.CHAT_COMPLETIONS) == [
        Capability.STRUCTURED_OUTPUT_RESPONSE_FORMAT
    ]
    rich = REQ.model_copy(
        update={
            "tools": [R_TOOL],
            "reasoning_effort": "none",
            "text_format": {"type": "json_schema"},
        }
    )
    assert (
        adapter.unverified_capabilities(
            adapter.responses_payload(rich, stream=True), Endpoint.RESPONSES
        )
        == []
    )


def test_unverified_capabilities_on_results() -> None:
    adapter, _ = adapter_for(A.OK_TEXT)
    req = CHAT.model_copy(update={"extra_body": {"response_format": {"type": "json_object"}}})
    result = adapter.create_chat_completion(req)
    assert result.unverified_capabilities == [Capability.STRUCTURED_OUTPUT_RESPONSE_FORMAT]


def test_stopping_a_stream_early_closes_the_response() -> None:
    closed: list[bool] = []

    class Body(httpx2.SyncByteStream):
        def __iter__(self):  # noqa: ANN204
            for _ in range(100):
                yield b'event: response.output_text.delta\ndata: {"delta": "a"}\n\n'

        def close(self) -> None:
            closed.append(True)

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, stream=Body())

    adapter, _ = make_adapter(httpx2.MockTransport(handler))
    stream = adapter.stream_response(REQ)
    assert next(stream).kind == "text_delta"
    stream.close()  # type: ignore[attr-defined]
    assert closed


def test_stream_sends_the_stream_flag_and_accept_header() -> None:
    adapter, bridge = adapter_for(A.OK_TEXT)
    collect(adapter.stream_response(REQ))
    request = bridge.requests[0]
    assert request.headers["accept"] == "text/event-stream"
    assert json.loads(request.content)["stream"] is True
    assert json.loads(request.content)["store"] is False


# --- raw_post (Phase 10 probes) -------------------------------------------------------------


def test_raw_post_refuses_contract_violations_by_default() -> None:
    adapter, bridge = adapter_for()
    with pytest.raises(ProviderError) as info:
        adapter.raw_post("responses", base_payload(store=True))
    assert info.value.kind is ErrorKind.UNSUPPORTED_CAPABILITY and bridge.requests == []


def test_raw_post_sends_violations_when_allowed_and_returns_the_400() -> None:
    adapter, bridge = adapter_for()
    raw = adapter.raw_post("/responses", base_payload(store=True), allow_contract_violation=True)
    assert raw.http_status == 400 and raw.request_id == "fc_mock_1"
    assert raw.body["error"]["code"] == "unsupported_parameter"
    assert "store=true" in raw.text and raw.headers["x-request-id"] == "fc_mock_1"
    assert json.loads(bridge.requests[0].content)["store"] is True  # sent exactly as given


def test_raw_post_does_not_retry_or_rewrite() -> None:
    adapter, bridge = adapter_for(A.HTTP_ERROR, A.OK_TEXT)
    payload = base_payload(max_output_tokens=10)
    raw = adapter.raw_post("responses", payload)
    assert raw.http_status == 503 and len(bridge.requests) == 1
    assert json.loads(bridge.requests[0].content) == payload


def test_raw_post_to_other_paths_skips_preflight() -> None:
    transport, seen = raw_transport(body=b"not json")
    adapter, _ = make_adapter(transport)
    raw = adapter.raw_post("probe/echo", {"store": True})
    assert raw.body is None and raw.text == "not json"
    assert str(seen[0].url) == "https://bharatcode.ai/api/model/v1/probe/echo"


@pytest.mark.parametrize(
    "path",
    [
        "https://evil.example/steal",
        "//evil.example/x",
        "../../other",
        "a/../../b",
        "http:/x",
        "\\\\x",
    ],
)
def test_raw_post_paths_must_stay_under_the_base_url(path: str) -> None:
    adapter, seen = offline_adapter()
    with pytest.raises(ProviderError) as info:
        adapter.raw_post(path, {"model": MODEL})
    assert info.value.kind is ErrorKind.BAD_REQUEST and seen == []


def test_raw_post_redacts_the_answer() -> None:
    key = "bc_live_" + "R4wEchoedKey77"
    transport, _ = raw_transport(
        body=json.dumps({"echo": key}).encode(), headers={"x-echo": key, "x-request-id": "r"}
    )
    adapter, _ = make_adapter(transport, api_key=key)
    raw = adapter.raw_post("probe", {})
    assert key not in raw.model_dump_json()


# --- Lifecycle and registry -----------------------------------------------------------------


def test_repr_and_close() -> None:
    transport, _ = raw_transport()
    with BharatCodeAdapter(make_config(), transport=transport) as adapter:
        assert repr(adapter) == (
            "BharatCodeAdapter(name='bharatcode', base_url='https://bharatcode.ai/api/model/v1')"
        )
        assert FAKE_KEY not in repr(adapter)
        assert adapter.profile.provider == "bharatcode"
    assert adapter._client.is_closed


def test_profile_follows_the_configured_base_url() -> None:
    transport, seen = raw_transport(body=b'{"data": []}')
    config = make_config().model_copy(update={"base_url": "http://127.0.0.1:8080/api/model/v1"})
    adapter = BharatCodeAdapter(config, transport=transport)
    adapter.list_models()
    assert adapter.profile.base_url == "http://127.0.0.1:8080/api/model/v1"
    assert str(seen[0].url) == "http://127.0.0.1:8080/api/model/v1/models"


def test_registry_builds_the_bharatcode_adapter() -> None:
    transport, _ = raw_transport()
    sleeps = Sleeps()
    adapter = build_adapter(make_config(), transport=transport, sleep=sleeps)
    assert isinstance(adapter, BharatCodeAdapter) and adapter._sleep is sleeps


def test_registry_honours_the_no_network_switch() -> None:
    with pytest.raises(NetworkDisabledError):
        build_adapter(make_config())


def test_registry_has_no_generic_adapter_yet() -> None:
    config = ProviderConfig(name="local", kind="openai_compatible", base_url="https://x.test/v1")
    with pytest.raises(ConfigError, match="P1"):
        build_adapter(config, transport=raw_transport()[0])
