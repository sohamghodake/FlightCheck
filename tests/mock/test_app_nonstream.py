"""Mock app, non-streaming: every route, every action, documented 400s, auth, limits, 500s.

httpx2's ASGITransport is async-only, so these tests use FastAPI's TestClient (a sync
httpx2.Client running the ASGI app in-process; no sockets).
"""

import json
from collections.abc import Callable

import openai
import pytest
from fastapi.testclient import TestClient
from openai.types import Model
from openai.types.chat import ChatCompletion
from openai.types.responses import Response

from flightcheck.core.models import Endpoint
from flightcheck.mock import (
    FaultScript,
    MockAction,
    MockStep,
    TrafficRecorder,
    create_mock_app,
    payloads,
)

FAKE = "fc-fake-key-0001"
AUTH = {"Authorization": f"Bearer {FAKE}"}
BASE = "/api/model/v1"
LIVE_KEY = "bc_live_" + "Pl4ntedS3cretValue"
SCHEMA = {
    "type": "object",
    "properties": {"city": {"type": "string"}, "days": {"type": "integer"}},
    "required": ["city", "days"],
}
RESPONSES_TOOL = {"type": "function", "name": "get_weather", "parameters": SCHEMA}
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


def responses_req(**extra: object) -> dict:
    return {"model": "qwen-3.8-27b", "input": "What is the weather?", "store": False, **extra}


def chat_req(**extra: object) -> dict:
    return {"model": "qwen-3.8-27b", "messages": [{"role": "user", "content": "hi"}], **extra}


def assert_error(resp, status: int, code: str, kind: str | None = None) -> dict:  # type: ignore[no-untyped-def]
    assert resp.status_code == status
    body = resp.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"message", "type", "code"}
    assert body["error"]["code"] == code
    if kind is not None:
        assert body["error"]["type"] == kind
    assert resp.headers["x-request-id"].startswith("fc_mock_")
    return body["error"]


# --- routes ---------------------------------------------------------------------------------


def test_models_route(make: Make) -> None:
    client, rec = make()
    resp = client.get(f"{BASE}/models", headers=AUTH)
    assert resp.status_code == 200 and resp.headers["x-request-id"] == "fc_mock_1"
    ids = [Model.model_validate(m).id for m in resp.json()["data"]]
    assert ids == ["deepseek-v4.1-flash", "qwen-3.8-27b"]
    (ex,) = rec.list()
    assert ex.endpoint is Endpoint.MODELS and ex.method == "GET" and ex.step_id == "models"
    assert ex.request_json is None and ex.response_status == 200


def test_models_route_does_not_consume_script(make: Make) -> None:
    client, rec = make(s(MockAction.HTTP_ERROR, status=503))
    assert client.get(f"{BASE}/models", headers=AUTH).status_code == 200
    assert client.post(f"{BASE}/responses", json=responses_req(), headers=AUTH).status_code == 503


def test_responses_and_chat_routes(make: Make) -> None:
    client, rec = make()
    r1 = client.post(f"{BASE}/responses", json=responses_req(), headers=AUTH)
    r2 = client.post(f"{BASE}/chat/completions", json=chat_req(), headers=AUTH)
    assert Response.model_validate(r1.json()).output_text == "FlightCheck mock answer [mock]"
    chat = ChatCompletion.model_validate(r2.json())
    assert chat.choices[0].message.content == "FlightCheck mock answer [mock]"
    assert [e.endpoint for e in rec.list()] == [Endpoint.RESPONSES, Endpoint.CHAT_COMPLETIONS]
    assert [e.path for e in rec.list()] == [f"{BASE}/responses", f"{BASE}/chat/completions"]
    assert all(e.step_id == "default" for e in rec.list())


def test_custom_base_path(make: Make) -> None:
    client, _ = make(base_path="/v1/")
    assert client.post("/v1/responses", json=responses_req(), headers=AUTH).status_code == 200
    assert client.post(f"{BASE}/responses", json=responses_req(), headers=AUTH).status_code == 404


def test_unknown_route_and_wrong_method_are_recorded(make: Make) -> None:
    client, rec = make()
    assert_error(client.post("/v1/responses", json=responses_req(), headers=AUTH), 404, "not_found")
    assert_error(client.get(f"{BASE}/responses", headers=AUTH), 405, "method_not_allowed")
    assert [(e.endpoint, e.step_id, e.response_status) for e in rec.list()] == [
        (None, "route", 404),
        (None, "route", 405),
    ]


def test_every_response_carries_its_request_id(make: Make) -> None:
    client, rec = make(s(MockAction.HTTP_ERROR, status=503))
    responses = [
        client.post(f"{BASE}/responses", json=responses_req(), headers=AUTH),  # 503
        client.post(f"{BASE}/responses", json=responses_req()),  # 401
        client.post(f"{BASE}/responses", content=b"{", headers=AUTH),  # 400
        client.post(f"{BASE}/responses", json=responses_req(store=True), headers=AUTH),  # 400
        client.post(f"{BASE}/responses", json=responses_req(stream=True), headers=AUTH),  # SSE
        client.get(f"{BASE}/models", headers=AUTH),  # 200
        client.get("/nowhere"),  # 404
    ]
    assert [r.status_code for r in responses] == [503, 401, 400, 400, 200, 200, 404]
    assert responses[4].headers["content-type"].startswith("text/event-stream")
    assert [r.headers["x-request-id"] for r in responses] == [f"fc_mock_{i}" for i in range(1, 8)]
    assert [e.seq for e in rec.list()] == list(range(1, 8))


# --- actions --------------------------------------------------------------------------------


@pytest.mark.parametrize("tool", [RESPONSES_TOOL, CHAT_TOOL])
def test_tool_call_on_responses(make: Make, tool: dict) -> None:
    client, rec = make(s(MockAction.TOOL_CALL))
    resp = client.post(f"{BASE}/responses", json=responses_req(tools=[tool]), headers=AUTH)
    (item,) = Response.model_validate(resp.json()).output
    assert item.type == "function_call" and item.call_id == "call_fc_S9_1"
    assert json.loads(item.arguments) == {"city": "fc_value", "days": 1}
    summary = rec.list()[0].response_summary
    assert summary["finish"] == "tool_call" and summary["call_ids"] == ["call_fc_S9_1"]
    assert summary["tool"] == "get_weather"
    assert summary["tool_style"] == ("chat" if "function" in tool else "responses")
    assert "no_tools" not in summary


@pytest.mark.parametrize("tool", [CHAT_TOOL, RESPONSES_TOOL])
def test_tool_call_on_chat(make: Make, tool: dict) -> None:
    client, rec = make(s(MockAction.TOOL_CALL))
    resp = client.post(f"{BASE}/chat/completions", json=chat_req(tools=[tool]), headers=AUTH)
    choice = ChatCompletion.model_validate(resp.json()).choices[0]
    assert choice.finish_reason == "tool_calls"
    (call,) = choice.message.tool_calls or []
    assert call.id == "call_fc_S9_1" and call.function.name == "get_weather"
    assert rec.list()[0].response_summary["call_ids"] == ["call_fc_S9_1"]


@pytest.mark.parametrize("path", ["responses", "chat/completions"])
@pytest.mark.parametrize("action", [MockAction.TOOL_CALL, MockAction.TRUNCATE_TOOL_CALL])
def test_tool_actions_without_tools_fall_back(make: Make, path: str, action: MockAction) -> None:
    client, rec = make(s(action))
    body = responses_req() if path == "responses" else chat_req()
    resp = client.post(f"{BASE}/{path}", json=body, headers=AUTH)
    assert resp.status_code == 200
    summary = rec.list()[0].response_summary
    assert summary["no_tools"] is True and summary["call_ids"] == []
    truncated = action is MockAction.TRUNCATE_TOOL_CALL
    assert summary["finish"] == ("truncated" if truncated else "completed")
    if path == "responses":
        expected = "FlightCheck partial [S9]" if truncated else "FlightCheck mock answer [S9]"
        assert Response.model_validate(resp.json()).output_text == expected


def test_truncate_actions(make: Make) -> None:
    client, rec = make(
        s(MockAction.TRUNCATE),
        s(MockAction.TRUNCATE),
        s(MockAction.TRUNCATE_TOOL_CALL),
        s(MockAction.TRUNCATE_TOOL_CALL),
    )
    r1 = client.post(f"{BASE}/responses", json=responses_req(), headers=AUTH).json()
    r2 = client.post(f"{BASE}/chat/completions", json=chat_req(), headers=AUTH).json()
    r3 = client.post(
        f"{BASE}/responses", json=responses_req(tools=[RESPONSES_TOOL]), headers=AUTH
    ).json()
    r4 = client.post(f"{BASE}/chat/completions", json=chat_req(tools=[CHAT_TOOL]), headers=AUTH)
    assert r1["status"] == "incomplete"
    assert r1["incomplete_details"] == {"reason": "max_output_tokens"}
    assert Response.model_validate(r1).output_text == "FlightCheck partial [S9]"
    assert ChatCompletion.model_validate(r2).choices[0].finish_reason == "length"
    assert Response.model_validate(r3).output[0].status == "incomplete"
    assert ChatCompletion.model_validate(r4.json()).choices[0].finish_reason == "length"
    assert [e.response_summary["finish"] for e in rec.list()] == [
        "truncated",
        "truncated",
        "truncated_tool_call",
        "truncated_tool_call",
    ]


@pytest.mark.parametrize("path", ["responses", "chat/completions"])
def test_http_error_action(make: Make, path: str) -> None:
    client, rec = make(s(MockAction.HTTP_ERROR, status=503, retry_after=3))
    body = responses_req() if path == "responses" else chat_req()
    resp = client.post(f"{BASE}/{path}", json=body, headers=AUTH)
    assert_error(resp, 503, None, "server_error")  # type: ignore[arg-type]
    assert resp.headers["retry-after"] == "3"
    ex = rec.list()[0]
    assert ex.step_id == "step_http_error" and ex.response_status == 503
    assert ex.response_summary["finish"] == "error"


def test_http_error_with_code(make: Make) -> None:
    client, _ = make(s(MockAction.HTTP_ERROR, status=429, code="rate_limited", message="slow"))
    resp = client.post(f"{BASE}/responses", json=responses_req(), headers=AUTH)
    err = assert_error(resp, 429, "rate_limited", "rate_limit_error")
    assert err["message"] == "slow"


@pytest.mark.parametrize("path", ["responses", "chat/completions"])
def test_stream_fail_after_non_stream_is_500(make: Make, path: str) -> None:
    client, rec = make(s(MockAction.STREAM_FAIL_AFTER, n=2))
    body = responses_req() if path == "responses" else chat_req()
    assert_error(client.post(f"{BASE}/{path}", json=body, headers=AUTH), 500, "server_error")
    assert rec.list()[0].step_id == "step_stream_fail_after"


@pytest.mark.parametrize("action", [MockAction.KEEPALIVE_STREAM, MockAction.OK_TEXT])
def test_text_like_actions(make: Make, action: MockAction) -> None:
    client, rec = make(s(action, text="custom answer"))
    resp = client.post(f"{BASE}/responses", json=responses_req(), headers=AUTH)
    assert Response.model_validate(resp.json()).output_text == "custom answer"
    assert rec.list()[0].response_summary["action"] == action.value


def test_delay_action(make: Make, monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("flightcheck.mock.app.asyncio.sleep", fake_sleep)
    client, rec = make(s(MockAction.DELAY, seconds=2.5))
    resp = client.post(f"{BASE}/chat/completions", json=chat_req(), headers=AUTH)
    assert ChatCompletion.model_validate(resp.json()).choices[0].message.content
    assert slept == [2.5]
    assert rec.list()[0].response_summary["delay_s"] == 2.5


def test_delay_really_waits(make: Make) -> None:
    client, rec = make(s(MockAction.DELAY, seconds=0.05))
    resp = client.post(f"{BASE}/responses", json=responses_req(), headers=AUTH)
    assert resp.status_code == 200
    assert rec.list()[0].at_ms >= 50


@pytest.mark.parametrize("seconds", [-1, 601, "2", True])
def test_bad_delay_is_a_recorded_500(make: Make, seconds: object) -> None:
    client, rec = make(s(MockAction.DELAY, seconds=seconds))
    resp = client.post(f"{BASE}/responses", json=responses_req(), headers=AUTH)
    assert_error(resp, 500, "flightcheck_mock_error")
    assert rec.list()[0].response_summary["error"] == "ValueError"


def test_script_then_default(make: Make) -> None:
    client, rec = make(s(MockAction.HTTP_ERROR, status=503), s(MockAction.TRUNCATE))
    codes = [
        client.post(f"{BASE}/responses", json=responses_req(), headers=AUTH).status_code
        for _ in range(3)
    ]
    assert codes == [503, 200, 200]
    assert [e.step_id for e in rec.list()] == ["step_http_error", "step_truncate", "default"]


# --- documented 400s (ADR-014) --------------------------------------------------------------

VIOLATIONS = [
    ("store", {"store": True}, "unsupported_parameter"),
    ("previous_response_id", {"previous_response_id": "resp_123"}, "unsupported_parameter"),
    ("tools", {"tools": [{"type": "web_search"}]}, "unsupported_tool_type"),
    ("tools", {"tools": [RESPONSES_TOOL, {"type": "file_search"}]}, "unsupported_tool_type"),
    ("background", {"background": True}, "unsupported_parameter"),
    (
        "input",
        {"input": [{"role": "user", "content": [{"type": "input_audio", "input_audio": {}}]}]},
        "unsupported_input_type",
    ),
    (
        "input",
        {"input": [{"role": "user", "content": [{"type": "input_file", "file_url": "x"}]}]},
        "unsupported_input_type",
    ),
    ("input", {"input": [{"role": "user", "content": [{"file_id": "f1"}]}]}, None),
    ("input", {"input": [{"role": "user", "content": [{"file_data": "QUJD"}]}]}, None),
]


@pytest.mark.parametrize(("field", "extra", "code"), VIOLATIONS)
def test_documented_400(make: Make, field: str, extra: dict, code: str | None) -> None:
    client, rec = make(s(MockAction.OK_TEXT))
    resp = client.post(f"{BASE}/responses", json=responses_req(**extra), headers=AUTH)
    assert_error(resp, 400, code or "unsupported_input_type", "invalid_request_error")
    ex = rec.list()[0]
    assert ex.step_id == "contract" and ex.response_summary["violations"] == [field]
    # The scripted step was not consumed by the rejected request.
    again = client.post(f"{BASE}/responses", json=responses_req(), headers=AUTH)
    assert rec.list()[1].step_id == "step_ok_text" and again.status_code == 200


ALLOWED = [
    {"store": False},
    {"previous_response_id": None},
    {"tools": [RESPONSES_TOOL]},
    {"tools": [CHAT_TOOL]},
    {"tools": []},
    {"background": False},
    {"input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}]},
    {"input": [{"role": "user", "content": [{"type": "input_image", "image_url": "data:x"}]}]},
    {"input": [{"type": "function_call_output", "call_id": "call_1", "output": "{}"}]},
    {"instructions": "be brief", "text": {"format": {"type": "text"}}},
]


@pytest.mark.parametrize("extra", ALLOWED)
def test_documented_400_lookalikes_are_accepted(make: Make, extra: dict) -> None:
    client, rec = make()
    resp = client.post(f"{BASE}/responses", json=responses_req(**extra), headers=AUTH)
    assert resp.status_code == 200
    assert "violations" not in rec.list()[0].response_summary


def test_store_omitted_is_accepted(make: Make) -> None:
    client, _ = make()
    body = {"model": "qwen-3.8-27b", "input": "hi"}
    assert client.post(f"{BASE}/responses", json=body, headers=AUTH).status_code == 200


def test_all_violations_recorded_first_one_reported(make: Make) -> None:
    client, rec = make()
    body = responses_req(store=True, background=True, tools=[{"type": "web_search"}])
    err = assert_error(
        client.post(f"{BASE}/responses", json=body, headers=AUTH), 400, "unsupported_parameter"
    )
    assert "store" in err["message"]
    assert rec.list()[0].response_summary["violations"] == ["store", "tools", "background"]


def test_documented_400s_apply_only_to_responses(make: Make) -> None:
    client, _ = make()
    body = chat_req(store=True, background=True, tools=[{"type": "web_search"}])
    assert client.post(f"{BASE}/chat/completions", json=body, headers=AUTH).status_code == 200


def test_documented_400_before_streaming(make: Make) -> None:
    client, _ = make()
    resp = client.post(
        f"{BASE}/responses", json=responses_req(store=True, stream=True), headers=AUTH
    )
    assert_error(resp, 400, "unsupported_parameter")


# --- JSON errors (streaming is covered in test_streaming.py) --------------------------------


@pytest.mark.parametrize("raw", [b"{not json", b"", b"[1, 2]", b'"text"', b"\xff\xfe"])
def test_invalid_json_is_400(make: Make, raw: bytes) -> None:
    client, rec = make()
    headers = {**AUTH, "Content-Type": "application/json"}
    resp = client.post(f"{BASE}/responses", content=raw, headers=headers)
    assert_error(resp, 400, "invalid_json", "invalid_request_error")
    ex = rec.list()[0]
    assert ex.request_json is None and ex.step_id == "invalid_json"
    assert len(ex.request_sha256) == 64


# --- auth -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": ""}, {"Authorization": "Bearer "}, {"Authorization": "Basic abc"}],
)
@pytest.mark.parametrize("path", ["responses", "chat/completions", "models"])
def test_missing_or_blank_bearer_is_401(make: Make, headers: dict, path: str) -> None:
    client, rec = make(s(MockAction.OK_TEXT))
    if path == "models":
        resp = client.get(f"{BASE}/models", headers=headers)
    else:
        resp = client.post(f"{BASE}/{path}", json=responses_req(), headers=headers)
    assert_error(resp, 401, "invalid_api_key", "authentication_error")
    ex = rec.list()[0]
    assert ex.step_id == "auth" and ex.response_summary["auth"] == "missing"
    assert ex.request_headers.get("authorization") in (None, "[REDACTED]")


def test_other_key_is_accepted_but_flagged(make: Make) -> None:
    client, rec = make()
    resp = client.post(
        f"{BASE}/responses", json=responses_req(), headers={"Authorization": "Bearer real-key-x"}
    )
    assert resp.status_code == 200
    ex = rec.list()[0]
    assert ex.response_summary["auth"] == "other_key"
    assert ex.request_headers["authorization"] == "[REDACTED]"


def test_fake_key_is_marked(make: Make) -> None:
    client, rec = make()
    client.post(f"{BASE}/responses", json=responses_req(), headers=AUTH)
    ex = rec.list()[0]
    assert ex.response_summary["auth"] == "fake_key"
    assert ex.request_headers["authorization"] == "[FAKE KEY]"


def test_without_fake_key_any_token_is_unchecked(make: Make) -> None:
    client, rec = make(fake_key=None)
    assert client.post(f"{BASE}/responses", json=responses_req(), headers=AUTH).status_code == 200
    ex = rec.list()[0]
    assert ex.response_summary["auth"] == "unchecked"
    assert ex.request_headers["authorization"] == "[REDACTED]"


# --- request limit and 500s -----------------------------------------------------------------


def test_request_limit(make: Make) -> None:
    client, rec = make(max_requests=3)
    statuses = [
        client.post(f"{BASE}/responses", json=responses_req(), headers=AUTH).status_code
        for _ in range(3)
    ]
    assert statuses == [200, 200, 200]
    over = client.post(f"{BASE}/chat/completions", json=chat_req(), headers=AUTH)
    assert_error(over, 429, "flightcheck_request_limit", "rate_limit_error")
    assert_error(client.get(f"{BASE}/models"), 429, "flightcheck_request_limit")
    assert [e.step_id for e in rec.list()][-2:] == ["request_limit", "request_limit"]
    assert len(rec) == 5


def test_default_request_limit_is_25(make: Make) -> None:
    client, _ = make()
    statuses = [client.get(f"{BASE}/models", headers=AUTH).status_code for _ in range(26)]
    assert statuses == [200] * 25 + [429]


def test_bad_step_params_become_recorded_500(make: Make) -> None:
    client, rec = make(s(MockAction.HTTP_ERROR, status="busy"))
    resp = client.post(f"{BASE}/responses", json=responses_req(), headers=AUTH)
    assert_error(resp, 500, "flightcheck_mock_error", "server_error")
    ex = rec.list()[0]
    assert ex.response_status == 500 and ex.step_id == "step_http_error"
    assert ex.request_json == responses_req()


def test_builder_exception_is_500_without_leaking(
    make: Make, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def boom(request_json: dict, step: MockStep, seq: int) -> payloads.Built:
        raise RuntimeError(f"exploded with {LIVE_KEY}")

    monkeypatch.setitem(payloads.CHAT_BUILDERS, "text", boom)
    client, rec = make()
    resp = client.post(f"{BASE}/chat/completions", json=chat_req(), headers=AUTH)
    err = assert_error(resp, 500, "flightcheck_mock_error")
    assert err["message"] == "FlightCheck mock error (RuntimeError)."
    assert LIVE_KEY not in resp.text and LIVE_KEY not in caplog.text
    assert rec.list()[0].response_summary == {
        "auth": "fake_key",
        "action": "ok_text",
        "error": "RuntimeError",
        "finish": "error",
        "error_code": "flightcheck_mock_error",
    }
    # the mock keeps serving after a failure
    monkeypatch.undo()
    ok = client.post(f"{BASE}/chat/completions", json=chat_req(), headers=AUTH)
    assert ok.status_code == 200


# --- secrets --------------------------------------------------------------------------------


def test_planted_live_key_is_absent_everywhere(make: Make) -> None:
    client, rec = make(s(MockAction.TOOL_CALL), s(MockAction.HTTP_ERROR, status=503))
    secret_headers = {"Authorization": f"Bearer {LIVE_KEY}", "X-Api-Key": LIVE_KEY}
    tool = {
        "type": "function",
        "name": "lookup",
        "parameters": {
            "type": "object",
            "properties": {"k": {"enum": [LIVE_KEY]}},
            "required": ["k"],
        },
    }
    body = responses_req(
        input=[{"role": "user", "content": f"use {LIVE_KEY}"}],
        metadata={"api_key": LIVE_KEY},
        tools=[tool],
    )
    responses = [
        client.post(f"{BASE}/responses", json=body, headers=secret_headers),
        client.post(
            f"{BASE}/chat/completions", json=chat_req(user=LIVE_KEY), headers=secret_headers
        ),
        client.post(f"{BASE}/responses", json={**body, "store": True}, headers=secret_headers),
        client.post(
            f"{BASE}/responses", content=f"{{oops {LIVE_KEY}".encode(), headers=secret_headers
        ),
        client.post(f"{BASE}/responses", json=body, headers={"X-Api-Key": LIVE_KEY}),
    ]
    assert [r.status_code for r in responses] == [200, 503, 400, 400, 401]
    for r in responses:
        assert LIVE_KEY not in r.text
        assert all(LIVE_KEY not in v for v in r.headers.values())
    recorded = "".join(e.model_dump_json() for e in rec.list())
    assert LIVE_KEY not in recorded
    assert "Pl4ntedS3cretValue" not in recorded
    assert {e.request_headers.get("authorization") for e in rec.list()} == {"[REDACTED]", None}


# --- OpenAI SDK end to end (in-process, no sockets) -----------------------------------------


def test_openai_sdk_parses_every_success_payload(make: Make) -> None:
    client, rec = make(
        s(MockAction.OK_TEXT),
        s(MockAction.TOOL_CALL),
        s(MockAction.TRUNCATE),
        s(MockAction.OK_TEXT),
        s(MockAction.TOOL_CALL),
    )
    sdk = openai.OpenAI(
        api_key=FAKE, base_url=f"http://127.0.0.1{BASE}", http_client=client, max_retries=0
    )
    first = sdk.responses.create(model="qwen-3.8-27b", input="hi", store=False)
    assert first.output_text == "FlightCheck mock answer [S9]"
    call = sdk.responses.create(
        model="qwen-3.8-27b", input="hi", store=False, tools=[RESPONSES_TOOL]
    ).output[0]
    assert call.type == "function_call" and call.call_id == "call_fc_S9_2"
    cut = sdk.responses.create(model="qwen-3.8-27b", input="hi", store=False)
    assert cut.status == "incomplete" and cut.output_text == "FlightCheck partial [S9]"
    chat = sdk.chat.completions.create(
        model="qwen-3.8-27b", messages=[{"role": "user", "content": "hi"}]
    )
    assert chat.choices[0].message.content == "FlightCheck mock answer [S9]"
    tool_chat = sdk.chat.completions.create(
        model="qwen-3.8-27b", messages=[{"role": "user", "content": "hi"}], tools=[CHAT_TOOL]
    )
    assert (tool_chat.choices[0].message.tool_calls or [])[0].id == "call_fc_S9_5"
    assert [m.id for m in sdk.models.list()] == ["deepseek-v4.1-flash", "qwen-3.8-27b"]
    with pytest.raises(openai.BadRequestError) as exc:
        sdk.responses.create(model="qwen-3.8-27b", input="hi", store=True)
    assert exc.value.code == "unsupported_parameter"
    assert {e.request_headers["authorization"] for e in rec.list()} == {"[FAKE KEY]"}
