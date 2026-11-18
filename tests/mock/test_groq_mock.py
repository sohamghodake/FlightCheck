"""Mock, Groq routes (phase G4, ADR-038): /openai/v1 routes, Groq shapes, Groq 400s, faults.

Every expectation traces to a docs/GROQ_CONTRACT.md row (cited per test). In-process through
FastAPI's TestClient (no sockets), except the one ``slow`` test that runs ``MockServer``.
"""

import json
from collections.abc import Callable

import httpx2
import openai
import pytest
from fastapi.testclient import TestClient

from flightcheck.core.config import GROQ_DEFAULT_MODEL, GROQ_DOCUMENTED_MODELS
from flightcheck.core.models import Endpoint
from flightcheck.mock import (
    FaultScript,
    MockAction,
    MockServer,
    MockStep,
    TrafficRecorder,
    create_mock_app,
    payloads,
)

FAKE = "fc-fake-key-0001"
AUTH = {"Authorization": f"Bearer {FAKE}"}
GROQ = "/openai/v1"
BC = "/api/model/v1"
SCHEMA = {
    "type": "object",
    "properties": {"city": {"type": "string"}},
    "required": ["city"],
}
CHAT_TOOL = {"type": "function", "function": {"name": "get_weather", "parameters": SCHEMA}}

Make = Callable[..., tuple[TestClient, TrafficRecorder]]


@pytest.fixture
def make() -> Make:
    def _make(*steps: MockStep, **kwargs: object) -> tuple[TestClient, TrafficRecorder]:
        recorder = TrafficRecorder()
        kwargs.setdefault("fake_key", FAKE)
        app = create_mock_app(FaultScript(steps=list(steps)), recorder, **kwargs)  # type: ignore[arg-type]
        return TestClient(app, base_url="http://127.0.0.1"), recorder

    return _make


def s(action: MockAction, **params: object) -> MockStep:
    return MockStep(id=f"step_{action.value}", action=action, params={"scenario": "S9", **params})


def chat(**extra: object) -> dict:
    return {
        "model": "openai/gpt-oss-20b",
        "messages": [{"role": "user", "content": "Weather in Delhi?"}],
        **extra,
    }


def responses(**extra: object) -> dict:
    return {"model": "openai/gpt-oss-20b", "input": "Weather in Delhi?", **extra}


def sdk(client: TestClient) -> openai.OpenAI:
    """The OpenAI SDK with Groq's base URL layout for it (``…/openai/v1``, contract K4)."""
    return openai.OpenAI(
        base_url=f"http://127.0.0.1{GROQ}", api_key=FAKE, http_client=client, max_retries=0
    )


def sse_lines(text: str) -> list[str]:
    return [line[len("data: ") :] for line in text.splitlines() if line.startswith("data: ")]


# --- routes and shapes ------------------------------------------------------------------------


def test_openai_sdk_and_groq_sdk_layouts_reach_the_groq_routes(make: Make) -> None:
    """K4: the OpenAI SDK needs base ``…/openai/v1``; the groq SDK uses the host root and puts
    ``/openai/v1`` in every path. Both land on the same Groq routes."""
    client, recorder = make()
    answer = sdk(client).chat.completions.create(**chat())  # type: ignore[call-overload]
    assert answer.choices[0].finish_reason == "stop"
    raw = client.post(f"{GROQ}/chat/completions", json=chat(), headers=AUTH)  # root base URL
    assert raw.status_code == 200
    first, second = recorder.list()
    assert first.path == second.path == "/openai/v1/chat/completions"
    assert first.endpoint is second.endpoint is Endpoint.CHAT_COMPLETIONS
    assert first.response_summary["provider"] == "groq"


def test_groq_chat_body_carries_groq_fields_and_no_request_id_header(make: Make) -> None:
    """C13/C14: x_groq.id, usage timing fields, system_fingerprint, service_tier; E10: no
    documented x-request-id header (the ID is x_groq.id)."""
    client, _ = make()
    response = client.post(f"{GROQ}/chat/completions", json=chat(), headers=AUTH)
    body = response.json()
    assert "x-request-id" not in response.headers
    assert body["x_groq"] == {"id": "req_fc_mock_1"}
    assert body["system_fingerprint"] == payloads.GROQ_SYSTEM_FINGERPRINT
    assert body["service_tier"] == "on_demand"
    for key in ("queue_time", "prompt_time", "completion_time", "total_time"):
        assert isinstance(body["usage"][key], float)
    assert (
        body["usage"]["total_tokens"]
        == body["usage"]["prompt_tokens"] + (body["usage"]["completion_tokens"])
    )
    parsed = sdk(client).chat.completions.create(**chat())  # type: ignore[call-overload]
    assert parsed.model_extra is not None and parsed.model_extra["x_groq"]["id"].startswith("req_")


def test_groq_responses_body_is_the_openai_shape(make: Make) -> None:
    """R2/§2: Responses bodies keep the OpenAI shape (no Groq-specific fields documented)."""
    client, _ = make()
    response = sdk(client).responses.create(**responses(store=False))  # type: ignore[call-overload]
    assert response.status == "completed" and response.output_text
    raw = client.post(f"{GROQ}/responses", json=responses(), headers=AUTH)
    assert "x_groq" not in raw.json() and "x-request-id" not in raw.headers


def test_missing_model_names_groq_default(make: Make) -> None:
    client, _ = make()
    body = client.post(f"{GROQ}/chat/completions", json={"messages": []}, headers=AUTH).json()
    assert body["model"] == GROQ_DEFAULT_MODEL


def test_bharatcode_routes_answer_exactly_as_before(make: Make) -> None:
    """ADR-038: the BharatCode family is untouched: x-request-id, OpenAI error code, no Groq
    fields, no provider/received_ms in the recorded summary."""
    client, recorder = make(s(MockAction.OK_TEXT), s(MockAction.HTTP_ERROR, status=503))
    ok = client.post(f"{BC}/chat/completions", json=chat(), headers=AUTH)
    assert ok.headers["x-request-id"] == "fc_mock_1"
    assert "x_groq" not in ok.json() and "service_tier" not in ok.json()
    assert set(ok.json()["usage"]) == {"prompt_tokens", "completion_tokens", "total_tokens"}
    busy = client.post(f"{BC}/chat/completions", json=chat(), headers=AUTH)
    assert busy.json() == {
        "error": {
            "message": "Model is busy, retry in a few seconds.",
            "type": "server_error",
            "code": None,
        }
    }
    for exchange in recorder.list():
        assert "provider" not in exchange.response_summary
        assert "received_ms" not in exchange.response_summary


def test_groq_models_list_is_the_dated_catalogue(make: Make) -> None:
    """§1.8 catalogue; the field shape is OpenAI's, [UNVERIFIED] for Groq (A6)."""
    client, _ = make()
    ids = [m.id for m in sdk(client).models.list()]
    assert ids == sorted(GROQ_DOCUMENTED_MODELS)


def test_unknown_groq_route_and_missing_auth_use_groq_error_shape(make: Make) -> None:
    client, recorder = make()
    missing = client.post(f"{GROQ}/embeddings", json={}, headers=AUTH)
    assert missing.status_code == 404 and set(missing.json()["error"]) == {"message", "type"}
    noauth = client.post(f"{GROQ}/chat/completions", json=chat())
    assert noauth.status_code == 401 and "code" not in noauth.json()["error"]
    route, auth = recorder.list()
    assert route.response_summary["provider"] == "groq" and route.step_id == "route"
    assert route.response_summary["error_code"] == "not_found"  # the mock's own record
    assert auth.response_summary["error_code"] == "invalid_api_key"


def test_reserved_base_path_is_refused() -> None:
    with pytest.raises(ValueError, match="reserved"):
        create_mock_app(FaultScript(), TrafficRecorder(), base_path="/openai/v1/")


def test_arrival_time_is_recorded_on_groq_routes(make: Make) -> None:
    client, recorder = make()
    client.post(f"{GROQ}/chat/completions", json=chat(), headers=AUTH)
    (exchange,) = recorder.list()
    received = exchange.response_summary["received_ms"]
    assert isinstance(received, int) and 0 <= received <= exchange.at_ms


# --- errors -----------------------------------------------------------------------------------


def test_groq_http_error_body_has_message_and_type_only(make: Make) -> None:
    """E1: {"error": {"message", "type"}}, no code. E3: 503 means "wait before retrying"."""
    client, recorder = make(s(MockAction.HTTP_ERROR))
    response = client.post(f"{GROQ}/chat/completions", json=chat(), headers=AUTH)
    assert response.status_code == 503
    error = response.json()["error"]
    assert set(error) == {"message", "type"} and error["type"] == "server_error"
    assert "busy" not in error["message"].lower() and "wait" in error["message"]
    assert recorder.list()[0].response_summary["finish"] == "error"


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (400, "invalid_request_error"),  # E1
        (413, "invalid_request_error"),  # E2 (type [UNVERIFIED])
        (422, "invalid_request_error"),  # E2 (type [UNVERIFIED])
        (498, "capacity_exceeded"),  # E2 names the error capacity_exceeded
        (429, "rate_limit_error"),
        (500, "server_error"),
    ],
)
def test_groq_statuses(make: Make, status: int, error_type: str) -> None:
    client, _ = make(s(MockAction.HTTP_ERROR, status=status))
    response = client.post(f"{GROQ}/chat/completions", json=chat(), headers=AUTH)
    assert response.status_code == status
    assert response.json()["error"]["type"] == error_type


def test_429_carries_retry_after_and_rate_limit_headers(make: Make) -> None:
    """E5: retry-after only on 429; E6: x-ratelimit-* headers."""
    headers = {
        "retry-after": "2",
        "x-ratelimit-limit-requests": "1000",
        "x-ratelimit-remaining-tokens": 0,
        "x-ratelimit-reset-tokens": "7.66s",
    }
    client, recorder = make(s(MockAction.HTTP_ERROR, status=429, headers=headers))
    response = client.post(f"{GROQ}/chat/completions", json=chat(), headers=AUTH)
    assert response.status_code == 429
    assert response.headers["retry-after"] == "2"
    assert response.headers["x-ratelimit-remaining-tokens"] == "0"
    assert response.headers["x-ratelimit-reset-tokens"] == "7.66s"
    assert recorder.list()[0].response_summary["retry_after"] == "2"


@pytest.mark.parametrize(
    "params",
    [
        {"status": 503, "headers": {"retry-after": "2"}},  # E5: only on 429
        {"status": 503, "retry_after": 2},
        {"status": 429, "headers": {"x-should-retry": "true"}},  # not an allowed header
        {"status": 429, "headers": ["retry-after"]},
        {"status": 429, "headers": {"retry-after": True}},
    ],
)
def test_invalid_error_scripts_are_recorded_mock_errors(make: Make, params: dict) -> None:
    client, recorder = make(s(MockAction.HTTP_ERROR, **params))
    response = client.post(f"{GROQ}/chat/completions", json=chat(), headers=AUTH)
    assert response.status_code == 500
    assert recorder.list()[0].response_summary["error_code"] == "flightcheck_mock_error"


def test_bharatcode_http_error_accepts_the_headers_parameter(make: Make) -> None:
    client, _ = make(s(MockAction.HTTP_ERROR, status=429, headers={"retry-after": "1"}))
    response = client.post(f"{BC}/chat/completions", json=chat(), headers=AUTH)
    assert response.headers["retry-after"] == "1" and response.headers["x-request-id"]


def test_stream_fail_after_on_a_non_stream_groq_request_is_a_500(make: Make) -> None:
    client, _ = make(s(MockAction.STREAM_FAIL_AFTER))
    response = client.post(f"{GROQ}/chat/completions", json=chat(), headers=AUTH)
    assert response.status_code == 500 and "code" not in response.json()["error"]


# --- documented Groq 400s (core.contract) ------------------------------------------------------


@pytest.mark.parametrize(
    ("extra", "field"),
    [
        ({"logprobs": True}, "logprobs"),  # C2
        ({"logit_bias": {"50256": -100}}, "logit_bias"),  # C2
        ({"top_logprobs": 2}, "top_logprobs"),  # C2
        ({"n": 2}, "n"),  # C4
        ({"reasoning_effort": "none"}, "reasoning_effort"),  # M4: GPT-OSS has no "none"
        (
            {"reasoning_format": "raw", "response_format": {"type": "json_object"}},
            "reasoning_format",
        ),  # S8
        ({"reasoning_format": "raw", "tools": [CHAT_TOOL]}, "reasoning_format"),  # S8
    ],
)
def test_documented_chat_400s(make: Make, extra: dict, field: str) -> None:
    client, recorder = make(s(MockAction.OK_TEXT))
    response = client.post(f"{GROQ}/chat/completions", json=chat(**extra), headers=AUTH)
    assert response.status_code == 400
    assert "[UNVERIFIED]" not in response.json()["error"]["message"]
    exchange = recorder.list()[0]
    assert exchange.step_id == "contract" and exchange.response_summary["violations"] == [field]
    assert "status_unverified" not in exchange.response_summary


@pytest.mark.parametrize(
    "extra",
    [
        {"logprobs": False},
        {"logprobs": None, "logit_bias": None},
        {"n": 1},
        {"reasoning_effort": "low"},
        {"reasoning_format": "parsed", "tools": [CHAT_TOOL]},
        {"reasoning_format": "raw"},
        # C2 vs C3 conflict: messages[].name is not enforced until G5 verifies it.
        {"messages": [{"role": "user", "content": "hi", "name": "alice"}]},
        # M4 judged only for catalogue models with documented values.
        {"model": "some/new-model", "reasoning_effort": "none"},
    ],
)
def test_chat_lookalikes_are_accepted(make: Make, extra: dict) -> None:
    client, _ = make()
    response = client.post(f"{GROQ}/chat/completions", json=chat(**extra), headers=AUTH)
    assert response.status_code == 200


@pytest.mark.parametrize(
    ("extra", "field"),
    [
        ({"store": True}, "store"),  # R5
        ({"previous_response_id": "resp_1"}, "previous_response_id"),  # R4
        ({"include": ["reasoning.encrypted_content"]}, "include"),  # R4
        ({"safety_identifier": "user-1"}, "safety_identifier"),  # R4
        ({"prompt_cache_key": "k"}, "prompt_cache_key"),  # R4
        ({"prompt": {"id": "pmpt_1"}}, "prompt"),  # R4
    ],
)
def test_responses_unsupported_params_get_an_unverified_400(
    make: Make, extra: dict, field: str
) -> None:
    """§2: Groq lists these as not supported but not their effect; the mock answers 400 and
    labels it [UNVERIFIED]."""
    client, recorder = make(s(MockAction.OK_TEXT))
    response = client.post(f"{GROQ}/responses", json=responses(**extra), headers=AUTH)
    assert response.status_code == 400
    assert "[UNVERIFIED]" in response.json()["error"]["message"]
    summary = recorder.list()[0].response_summary
    assert summary["violations"] == [field] and summary["status_unverified"] is True


def test_responses_reasoning_effort_outside_the_model_set_is_a_documented_400(
    make: Make,
) -> None:
    client, _ = make()
    body = responses(reasoning={"effort": "none"})
    response = client.post(f"{GROQ}/responses", json=body, headers=AUTH)
    assert response.status_code == 400
    assert "[UNVERIFIED]" not in response.json()["error"]["message"]


@pytest.mark.parametrize(
    "extra",
    [
        {"store": False},
        {"store": None},
        {"previous_response_id": None},
        # Built-in tools are not rejected by the mock (R9: error on other models is
        # [UNVERIFIED]); FC-RT-015 judges them from the traffic.
        {"tools": [{"type": "browser_search"}], "model": "qwen/qwen3.8-27b"},
        {"background": True},  # R8: undocumented on Groq, so not enforced
    ],
)
def test_responses_lookalikes_are_accepted(make: Make, extra: dict) -> None:
    client, _ = make()
    response = client.post(f"{GROQ}/responses", json=responses(**extra), headers=AUTH)
    assert response.status_code == 200


def test_bharatcode_still_rejects_what_groq_accepts(make: Make) -> None:
    client, _ = make()
    hosted = {"tools": [{"type": "browser_search"}]}
    assert client.post(f"{BC}/responses", json=responses(**hosted), headers=AUTH).status_code == 400
    assert client.post(f"{GROQ}/responses", json=responses(**hosted), headers=AUTH).status_code == (
        200
    )


# --- streams ----------------------------------------------------------------------------------


def test_groq_chat_stream_carries_x_groq_in_first_and_final_chunk(make: Make) -> None:
    """ST1: data-only SSE ending with [DONE]; ST2: x_groq.id in the first and final chunk,
    x_groq.usage in the final one, top-level usage with include_usage."""
    client, recorder = make()
    body = chat(stream=True, stream_options={"include_usage": True})
    response = client.post(f"{GROQ}/chat/completions", json=body, headers=AUTH)
    assert "x-request-id" not in response.headers
    lines = sse_lines(response.text)
    assert lines[-1] == "[DONE]"
    chunks = [json.loads(line) for line in lines[:-1]]
    assert chunks[0]["x_groq"] == {"id": "req_fc_mock_1"}
    assert all("x_groq" not in c for c in chunks[1:-1])
    final = chunks[-1]
    assert final["choices"][0]["finish_reason"] == "stop"
    assert final["x_groq"]["id"] == "req_fc_mock_1"
    assert final["x_groq"]["usage"]["total_time"] == payloads.GROQ_USAGE_TIMES["total_time"]
    assert final["usage"] == final["x_groq"]["usage"]
    assert recorder.list()[0].response_summary["finish"] == "completed"


def test_groq_chat_stream_without_include_usage_has_no_top_level_usage(make: Make) -> None:
    client, _ = make()
    response = client.post(f"{GROQ}/chat/completions", json=chat(stream=True), headers=AUTH)
    final = json.loads(sse_lines(response.text)[-2])
    assert "usage" not in final and "usage" in final["x_groq"]


def test_groq_stream_failure_ends_with_an_x_groq_error_chunk(make: Make) -> None:
    """ST3: a chunk carries x_groq.error; the wire format around it is [UNVERIFIED]: the mock
    sends no choices in it and ends without [DONE]."""
    client, recorder = make(s(MockAction.STREAM_FAIL_AFTER, n=2))
    response = client.post(f"{GROQ}/chat/completions", json=chat(stream=True), headers=AUTH)
    lines = sse_lines(response.text)
    assert "[DONE]" not in lines
    chunks = [json.loads(line) for line in lines]
    assert [c["choices"][0]["delta"].get("content") for c in chunks[1:3]] == [
        "FlightCheck ",
        "mock ",
    ]
    last = chunks[-1]
    assert last["choices"] == [] and last["x_groq"]["error"] == payloads.GROQ_STREAM_ERROR
    summary = recorder.list()[0].response_summary
    assert summary["finish"] == "x_groq_error" and summary["deltas"] == 2
    assert summary["done_sent"] is False


def test_openai_sdk_reads_a_groq_failed_stream_without_raising(make: Make) -> None:
    """Why FC-RT-030 matters: the SDK raises only for a top-level error, so an app must look
    at x_groq.error or the missing finish_reason itself."""
    client, _ = make(s(MockAction.STREAM_FAIL_AFTER, n=1))
    stream = sdk(client).chat.completions.create(**chat(stream=True))  # type: ignore[call-overload]
    chunks = list(stream)
    assert chunks[-1].choices == []
    assert (chunks[-1].model_extra or {})["x_groq"]["error"]
    assert all(c.choices[0].finish_reason is None for c in chunks if c.choices)


def test_groq_responses_stream_and_tool_stream(make: Make) -> None:
    """R15: Responses events are OpenAI's ([UNVERIFIED] for Groq); the SDK helper parses them."""
    client, _ = make(s(MockAction.TOOL_CALL), s(MockAction.STREAM_FAIL_AFTER))
    with sdk(client).responses.stream(  # type: ignore[call-overload]
        **responses(tools=[{"type": "function", "name": "get_weather", "parameters": SCHEMA}])
    ) as stream:
        final = stream.get_final_response()
    assert final.output[0].type == "function_call"
    response = client.post(f"{GROQ}/responses", json=responses(stream=True), headers=AUTH)
    assert "event: response.failed" in response.text and "x-request-id" not in response.headers


def test_bharatcode_chat_stream_is_unchanged(make: Make) -> None:
    client, _ = make(s(MockAction.STREAM_FAIL_AFTER, n=1))
    response = client.post(f"{BC}/chat/completions", json=chat(stream=True), headers=AUTH)
    chunks = [json.loads(line) for line in sse_lines(response.text)]
    assert all("x_groq" not in c for c in chunks) and len(chunks) == 2
    assert response.headers["x-request-id"] == "fc_mock_1"


# --- tool faults ------------------------------------------------------------------------------


def test_groq_tool_call_shape(make: Make) -> None:
    """T4: finish_reason tool_calls, id, type function, arguments a JSON string."""
    client, _ = make(s(MockAction.TOOL_CALL))
    answer = sdk(client).chat.completions.create(**chat(tools=[CHAT_TOOL]))  # type: ignore[call-overload]
    choice = answer.choices[0]
    assert choice.finish_reason == "tool_calls"
    call = choice.message.tool_calls[0]
    assert call.type == "function" and json.loads(call.function.arguments) == {"city": "fc_value"}


def test_tool_validation_error_is_groqs_400_with_failed_generation(make: Make) -> None:
    """T6: 400 with error.failed_generation (format [UNVERIFIED]: a JSON string here)."""
    client, recorder = make(s(MockAction.TOOL_VALIDATION_ERROR))
    with pytest.raises(openai.BadRequestError) as caught:
        sdk(client).chat.completions.create(**chat(tools=[CHAT_TOOL]))  # type: ignore[call-overload]
    error = caught.value.body
    assert isinstance(error, dict)
    generation = json.loads(error["failed_generation"])
    assert generation["tool_call_id"] == "call_fc_S9_1" and generation["name"] == "get_weather"
    summary = recorder.list()[0].response_summary
    assert summary["tool"] == "get_weather" and summary["error_code"] == "tool_validation_failed"


def test_tool_validation_error_is_not_a_bharatcode_fault(make: Make) -> None:
    client, recorder = make(s(MockAction.TOOL_VALIDATION_ERROR))
    response = client.post(f"{BC}/chat/completions", json=chat(tools=[CHAT_TOOL]), headers=AUTH)
    assert response.status_code == 500
    assert recorder.list()[0].response_summary["error_code"] == "flightcheck_mock_error"


# --- real server --------------------------------------------------------------------------------


@pytest.mark.slow
def test_mock_server_serves_both_layouts_on_127_0_0_1(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(name, raising=False)
    with MockServer(FaultScript(), fake_key=FAKE) as server:
        assert server.groq_base_url == f"{server.origin}/openai/v1"
        assert server.origin.startswith("http://127.0.0.1:")
        client = openai.OpenAI(base_url=server.groq_base_url, api_key=FAKE, max_retries=0)
        assert client.chat.completions.create(**chat()).choices  # type: ignore[call-overload]
        raw = httpx2.post(f"{server.origin}{GROQ}/chat/completions", json=chat(), headers=AUTH)
        assert raw.status_code == 200 and raw.json()["x_groq"]["id"] == "req_fc_mock_2"
    assert [e.path for e in server.recorder.list()] == ["/openai/v1/chat/completions"] * 2
