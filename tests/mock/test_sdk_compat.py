"""The official OpenAI Python SDK consumes every mock payload over a real localhost socket.

Plan §27 Phase 5B task 5 / §18.2 "SDK compatibility requirement". Only 127.0.0.1 is contacted.
"""

from __future__ import annotations

from collections.abc import Iterator

import openai
import pytest

from flightcheck.mock import FaultScript, MockAction, MockServer, MockStep

pytestmark = pytest.mark.slow

SCHEMA = {
    "type": "object",
    "properties": {"city": {"type": "string"}},
    "required": ["city"],
    "additionalProperties": False,
}
RESPONSES_TOOL = {"type": "function", "name": "get_weather", "parameters": SCHEMA, "strict": True}
CHAT_TOOL = {"type": "function", "function": {"name": "get_weather", "parameters": SCHEMA}}
MESSAGES = [{"role": "user", "content": "Weather in Delhi?"}]
MODEL = "qwen-3.8-27b"


def step(action: MockAction, **params: object) -> MockStep:
    return MockStep(id=action.value, action=action, params={"scenario": "S1", **params})


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")


@pytest.fixture
def run() -> Iterator:  # type: ignore[type-arg]
    servers: list[MockServer] = []

    def _run(*steps: MockStep) -> tuple[openai.OpenAI, MockServer]:
        server = MockServer(FaultScript(steps=list(steps)), fake_key="fc_fake").start()
        servers.append(server)
        sdk = openai.OpenAI(base_url=server.base_url, api_key="fc_fake", max_retries=0)
        return sdk, server

    yield _run
    for server in servers:
        server.stop()


def test_responses_text(run) -> None:  # type: ignore[no-untyped-def]
    sdk, server = run(step(MockAction.OK_TEXT))
    resp = sdk.responses.create(model=MODEL, input="hi", store=False)
    assert resp.output_text == "FlightCheck mock answer [S1]"
    assert resp.status == "completed"
    server.stop()
    assert server.recorder.list()[0].request_headers["authorization"] == "[FAKE KEY]"


def test_responses_tool_call(run) -> None:  # type: ignore[no-untyped-def]
    sdk, _ = run(step(MockAction.TOOL_CALL))
    resp = sdk.responses.create(model=MODEL, input="hi", store=False, tools=[RESPONSES_TOOL])
    calls = [item for item in resp.output if item.type == "function_call"]
    assert len(calls) == 1
    assert calls[0].call_id == "call_fc_S1_1" and calls[0].name == "get_weather"
    assert calls[0].arguments == '{"city":"fc_value"}'


def test_responses_truncated(run) -> None:  # type: ignore[no-untyped-def]
    sdk, _ = run(step(MockAction.TRUNCATE))
    resp = sdk.responses.create(model=MODEL, input="hi", store=False)
    assert resp.status == "incomplete"
    assert resp.incomplete_details is not None
    assert resp.incomplete_details.reason == "max_output_tokens"
    assert resp.output_text == "FlightCheck partial [S1]"


def test_chat_text(run) -> None:  # type: ignore[no-untyped-def]
    sdk, _ = run(step(MockAction.OK_TEXT))
    resp = sdk.chat.completions.create(model=MODEL, messages=MESSAGES)
    assert resp.choices[0].message.content == "FlightCheck mock answer [S1]"
    assert resp.choices[0].finish_reason == "stop"


def test_chat_tool_call(run) -> None:  # type: ignore[no-untyped-def]
    sdk, _ = run(step(MockAction.TOOL_CALL))
    resp = sdk.chat.completions.create(model=MODEL, messages=MESSAGES, tools=[CHAT_TOOL])
    (call,) = resp.choices[0].message.tool_calls or []
    assert call.id == "call_fc_S1_1" and call.function.name == "get_weather"
    assert resp.choices[0].finish_reason == "tool_calls"


def test_models_list(run) -> None:  # type: ignore[no-untyped-def]
    sdk, _ = run()
    assert [m.id for m in sdk.models.list()] == ["deepseek-v4.1-flash", "qwen-3.8-27b"]


@pytest.mark.parametrize("action", [MockAction.OK_TEXT, MockAction.KEEPALIVE_STREAM])
def test_responses_stream(run, action: MockAction) -> None:  # type: ignore[no-untyped-def]
    sdk, server = run(step(action))
    events = list(sdk.responses.create(model=MODEL, input="hi", store=False, stream=True))
    text = "".join(e.delta for e in events if e.type == "response.output_text.delta")
    assert text == "FlightCheck mock answer [S1]"
    assert events[-1].type == "response.completed"
    assert events[-1].response.output_text == text
    server.stop()  # streams are recorded after their last byte; stop() waits for that
    summary = server.recorder.list()[0].response_summary
    assert summary["finish"] == "completed" and summary["chunks_sent"] == summary["chunks_planned"]


def test_responses_stream_helper_tool_call(run) -> None:  # type: ignore[no-untyped-def]
    sdk, _ = run(step(MockAction.TOOL_CALL))
    with sdk.responses.stream(model=MODEL, input="hi", store=False, tools=[RESPONSES_TOOL]) as st:
        types = [event.type for event in st]
        final = st.get_final_response()
    assert "response.function_call_arguments.done" in types
    (call,) = final.output
    assert call.type == "function_call" and call.arguments == '{"city":"fc_value"}'


def test_responses_stream_helper_text(run) -> None:  # type: ignore[no-untyped-def]
    sdk, _ = run(step(MockAction.OK_TEXT))
    with sdk.responses.stream(model=MODEL, input="hi", store=False) as st:
        deltas = [e.delta for e in st if e.type == "response.output_text.delta"]
        final = st.get_final_response()
    assert "".join(deltas) == final.output_text == "FlightCheck mock answer [S1]"


def test_responses_stream_incomplete_and_failed(run) -> None:  # type: ignore[no-untyped-def]
    sdk, _ = run(step(MockAction.TRUNCATE), step(MockAction.STREAM_FAIL_AFTER, n=2))
    cut = list(sdk.responses.create(model=MODEL, input="hi", store=False, stream=True))
    assert cut[-1].type == "response.incomplete"
    failed = list(sdk.responses.create(model=MODEL, input="hi", store=False, stream=True))
    assert failed[-1].type == "response.failed"
    assert "".join(e.delta for e in failed if e.type == "response.output_text.delta") == (
        "FlightCheck mock "
    )


@pytest.mark.parametrize("action", [MockAction.OK_TEXT, MockAction.KEEPALIVE_STREAM])
def test_chat_stream(run, action: MockAction) -> None:  # type: ignore[no-untyped-def]
    sdk, _ = run(step(action))
    chunks = list(sdk.chat.completions.create(model=MODEL, messages=MESSAGES, stream=True))
    text = "".join(c.choices[0].delta.content or "" for c in chunks if c.choices)
    assert text == "FlightCheck mock answer [S1]"
    assert chunks[-1].choices[0].finish_reason == "stop"


def test_chat_stream_tool_call_and_truncation(run) -> None:  # type: ignore[no-untyped-def]
    sdk, _ = run(step(MockAction.TOOL_CALL), step(MockAction.TRUNCATE))
    chunks = list(
        sdk.chat.completions.create(model=MODEL, messages=MESSAGES, tools=[CHAT_TOOL], stream=True)
    )
    args = "".join(
        tc.function.arguments or ""
        for c in chunks
        for tc in (c.choices[0].delta.tool_calls or [])
        if tc.function is not None
    )
    assert args == '{"city":"fc_value"}'
    assert chunks[-1].choices[0].finish_reason == "tool_calls"
    cut = list(sdk.chat.completions.create(model=MODEL, messages=MESSAGES, stream=True))
    assert cut[-1].choices[0].finish_reason == "length"


def test_chat_stream_fail_after_ends_without_finish(run) -> None:  # type: ignore[no-untyped-def]
    sdk, server = run(step(MockAction.STREAM_FAIL_AFTER, n=2))
    chunks = list(sdk.chat.completions.create(model=MODEL, messages=MESSAGES, stream=True))
    assert all(c.choices[0].finish_reason is None for c in chunks)
    server.stop()
    assert server.recorder.list()[0].response_summary["finish"] == "eof_without_done"


def test_sdk_errors(run) -> None:  # type: ignore[no-untyped-def]
    sdk, _ = run(step(MockAction.HTTP_ERROR, status=503))
    with pytest.raises(openai.InternalServerError) as busy:
        sdk.responses.create(model=MODEL, input="hi", store=False)
    assert busy.value.status_code == 503
    assert busy.value.response.headers["x-request-id"] == "fc_mock_1"
    with pytest.raises(openai.BadRequestError) as bad:
        sdk.responses.create(model=MODEL, input="hi", store=True)
    assert bad.value.code == "unsupported_parameter"
    # The SDK always sends a bearer token; another scheme counts as "missing" for the mock.
    nokey = openai.OpenAI(
        base_url=sdk.base_url,
        api_key="unused",
        max_retries=0,
        default_headers={"Authorization": "Basic abc"},
    )
    with pytest.raises(openai.AuthenticationError):
        nokey.models.list()
