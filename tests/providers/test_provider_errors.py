"""Error normalisation (plan §14.4, §20.11) and secret hygiene of the provider layer."""

from __future__ import annotations

import json
import logging

import httpx2
import pytest
from provider_bridge import make_adapter, make_config, mock_transport, raw_transport, steps

from flightcheck.core.errors import PROVIDER_MESSAGE_LIMIT, ErrorKind, ProviderError
from flightcheck.core.redact import clear_registered_secrets, register_secret
from flightcheck.mock.faults import MockAction as A
from flightcheck.providers.base import ChatRequest, ResponsesRequest
from flightcheck.providers.http import error_from_response, error_from_transport, kind_for_status

REQ = ResponsesRequest(model="qwen-3.8-27b", input="hi")
CHAT = ChatRequest(model="qwen-3.8-27b", messages=[{"role": "user", "content": "hi"}])
LIVE_KEY = "bc_live_" + "Pl4ntedAdapterKey0042"


@pytest.fixture(autouse=True)
def _forget_secrets():  # noqa: ANN202
    yield
    clear_registered_secrets()


# --- Status first ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (400, ErrorKind.BAD_REQUEST),
        (401, ErrorKind.AUTH),
        (403, ErrorKind.FORBIDDEN),
        (404, ErrorKind.NOT_FOUND),
        (405, ErrorKind.BAD_REQUEST),
        (409, ErrorKind.BAD_REQUEST),
        (422, ErrorKind.UNPROCESSABLE),
        (429, ErrorKind.RATE_LIMITED),
        (500, ErrorKind.SERVER),
        (502, ErrorKind.SERVER),
        (503, ErrorKind.BUSY),
        (504, ErrorKind.SERVER),
        (302, ErrorKind.MALFORMED_RESPONSE),
    ],
)
def test_kind_for_status(status: int, kind: ErrorKind) -> None:
    assert kind_for_status(status) is kind


@pytest.mark.parametrize(
    ("status", "retryable"), [(503, True), (429, True), (500, False), (400, False), (401, False)]
)
def test_only_busy_and_rate_limit_answers_are_retryable(status: int, retryable: bool) -> None:
    error = error_from_response(status, httpx2.Headers(), "{}")
    assert error.retryable is retryable and error.http_status == status


def test_body_hints_do_not_override_status_except_model_not_in_plan() -> None:
    # A busy-looking code on a 400 stays a 400.
    body = json.dumps({"error": {"message": "x", "code": "server_busy"}})
    assert error_from_response(400, httpx2.Headers(), body).kind is ErrorKind.BAD_REQUEST


@pytest.mark.parametrize("status", [400, 403, 404, 422, 500])
def test_model_not_in_plan_is_forbidden_whatever_the_status(status: int) -> None:
    body = json.dumps(
        {"error": {"message": "Your plan does not include it", "code": "model_not_in_plan"}}
    )
    error = error_from_response(status, httpx2.Headers(), body)
    assert error.kind is ErrorKind.FORBIDDEN
    assert error.http_status == status and not error.retryable
    assert "model_not_in_plan" in error.message


def test_model_not_in_plan_in_plain_text_body() -> None:
    error = error_from_response(400, httpx2.Headers(), "refused: model_not_in_plan")
    assert error.kind is ErrorKind.FORBIDDEN


def test_model_not_in_plan_through_the_adapter() -> None:
    bridge = mock_transport(
        steps((A.HTTP_ERROR, {"status": 403, "code": "model_not_in_plan", "message": "Pro only"}))
    )
    adapter, sleeps = make_adapter(bridge.transport)
    with pytest.raises(ProviderError) as info:
        adapter.create_response(REQ.model_copy(update={"model": "deepseek-v4.1-flash"}))
    assert info.value.kind is ErrorKind.FORBIDDEN and sleeps == []
    assert "Pro only" in info.value.message and info.value.request_id == "fc_mock_1"


def test_error_message_names_status_code_and_request_id() -> None:
    headers = httpx2.Headers({"x-request-id": "req_123"})
    body = json.dumps(
        {"error": {"message": "store=true is not supported", "code": "unsupported_parameter"}}
    )
    error = error_from_response(400, headers, body, provider="bharatcode")
    assert error.message == (
        "bharatcode answered HTTP 400 (bad_request) [unsupported_parameter]: "
        "store=true is not supported (x-request-id: req_123)"
    )
    assert error.request_id == "req_123"


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("", "(empty body)"),
        ("<html>Bad Gateway</html>", "<html>Bad Gateway</html>"),
        ('{"error": "plain string"}', "plain string"),
        ('{"message": "top-level message"}', "top-level message"),
        ("[1, 2]", "[1, 2]"),
    ],
)
def test_error_bodies_of_other_shapes(body: str, expected: str) -> None:
    error = error_from_response(502, httpx2.Headers(), body)
    assert expected in error.message
    assert "x-request-id" not in error.message and error.request_id is None


def test_error_message_is_truncated() -> None:
    error = error_from_response(500, httpx2.Headers(), "x" * 5000)
    assert len(error.message) == PROVIDER_MESSAGE_LIMIT and error.message.endswith("…")


@pytest.mark.parametrize(
    ("exc", "kind", "retryable"),
    [
        (httpx2.ConnectError("refused"), ErrorKind.CONNECTION, True),
        (httpx2.ReadError("reset"), ErrorKind.CONNECTION, True),
        (httpx2.RemoteProtocolError("bad"), ErrorKind.CONNECTION, True),
        (httpx2.ConnectTimeout("slow"), ErrorKind.TIMEOUT, False),
        (httpx2.ReadTimeout("slow"), ErrorKind.TIMEOUT, False),
        (httpx2.PoolTimeout("slow"), ErrorKind.TIMEOUT, False),
        (httpx2.UnsupportedProtocol("ftp"), ErrorKind.CONNECTION, False),
        (httpx2.InvalidURL("bad"), ErrorKind.CONNECTION, False),
        (httpx2.DecodingError("gzip"), ErrorKind.MALFORMED_RESPONSE, False),
        (httpx2.TooManyRedirects("loop"), ErrorKind.CONNECTION, False),
    ],
)
def test_error_from_transport(exc: Exception, kind: ErrorKind, retryable: bool) -> None:
    error = error_from_transport(exc, provider="bharatcode")
    assert error.kind is kind and error.retryable is retryable
    assert type(exc).__name__ in error.message
    assert str(exc) not in error.message  # exception text can carry the URL


# --- Malformed answers (§20.11) -------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "problem"),
    [
        (b"not json at all", "is not valid JSON"),
        (b"", "is not valid JSON"),
        (b"[1, 2, 3]", "is not a JSON object"),
        (b'"a string"', "is not a JSON object"),
    ],
)
def test_malformed_json_is_malformed_response(body: bytes, problem: str) -> None:
    transport, _ = raw_transport(body=body, headers={"x-request-id": "rid-1"})
    adapter, sleeps = make_adapter(transport)
    with pytest.raises(ProviderError) as info:
        adapter.create_response(REQ)
    error = info.value
    assert error.kind is ErrorKind.MALFORMED_RESPONSE and error.http_status == 200
    assert problem in error.message and error.request_id == "rid-1"
    assert sleeps == []


def test_malformed_body_preview_is_capped() -> None:
    transport, _ = raw_transport(body=b"<" * 3000)
    adapter, _ = make_adapter(transport)
    with pytest.raises(ProviderError) as info:
        adapter.create_response(REQ)
    assert info.value.message.count("<") <= PROVIDER_MESSAGE_LIMIT


@pytest.mark.parametrize(
    "body",
    [
        {},  # no output
        {"status": "completed", "output": "text"},
        {"status": "completed", "output": [1]},
        {"status": "completed", "output": [{"type": "function_call", "name": "f"}]},
        {
            "status": "completed",
            "output": [{"type": "function_call", "call_id": 1, "name": "f", "arguments": "{}"}],
        },
        {
            "status": "completed",
            "output": [{"type": "function_call", "call_id": "c", "name": "f", "arguments": {}}],
        },
        {
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text"}]}],
        },
        {
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": 3}]}],
        },
    ],
)
def test_responses_body_missing_fields_is_malformed(body: dict) -> None:
    transport, _ = raw_transport(body=json.dumps(body).encode())
    adapter, _ = make_adapter(transport)
    with pytest.raises(ProviderError) as info:
        adapter.create_response(REQ)
    assert info.value.kind is ErrorKind.MALFORMED_RESPONSE
    assert "unexpected body" in info.value.message


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"choices": []},
        {"choices": [{}]},
        {"choices": [{"message": {"content": ["parts"]}}]},
        {"choices": [{"message": {"tool_calls": [{"id": "c"}]}, "finish_reason": "tool_calls"}]},
        {"choices": [{"message": {"tool_calls": "nope"}}]},
    ],
)
def test_chat_body_missing_fields_is_malformed(body: dict) -> None:
    transport, _ = raw_transport(body=json.dumps(body).encode())
    adapter, _ = make_adapter(transport)
    with pytest.raises(ProviderError) as info:
        adapter.create_chat_completion(CHAT)
    assert info.value.kind is ErrorKind.MALFORMED_RESPONSE


@pytest.mark.parametrize(
    "body", [{}, {"data": "x"}, {"data": [{"object": "model"}]}, {"data": [{"id": 5}]}]
)
def test_models_body_missing_fields_is_malformed(body: dict) -> None:
    transport, _ = raw_transport(body=json.dumps(body).encode())
    adapter, _ = make_adapter(transport)
    with pytest.raises(ProviderError) as info:
        adapter.list_models()
    assert info.value.kind is ErrorKind.MALFORMED_RESPONSE


def test_malformed_stream_event_is_malformed_response() -> None:
    transport, _ = raw_transport(
        body=b"event: response.output_text.delta\ndata: {broken\n\n",
        headers={"content-type": "text/event-stream", "x-request-id": "rid-s"},
    )
    adapter, _ = make_adapter(transport)
    with pytest.raises(ProviderError) as info:
        list(adapter.stream_response(REQ))
    assert info.value.kind is ErrorKind.MALFORMED_RESPONSE and info.value.request_id == "rid-s"


def test_json_answer_to_a_stream_request_is_malformed() -> None:
    transport, _ = raw_transport(
        body=b'{"output": []}', headers={"content-type": "application/json"}
    )
    adapter, _ = make_adapter(transport)
    with pytest.raises(ProviderError, match="JSON instead of SSE") as info:
        list(adapter.stream_chat_completion(CHAT))
    assert info.value.kind is ErrorKind.MALFORMED_RESPONSE


def test_stream_error_status_is_mapped_before_any_event() -> None:
    transport, _ = raw_transport(
        status=401, body=b'{"error": {"message": "invalid key"}}', headers={"x-request-id": "r4"}
    )
    adapter, _ = make_adapter(transport)
    stream = adapter.stream_response(REQ)
    with pytest.raises(ProviderError) as info:
        next(stream)
    assert info.value.kind is ErrorKind.AUTH and "invalid key" in info.value.message
    assert info.value.request_id == "r4"


def test_unserialisable_payload_is_a_provider_error() -> None:
    transport, seen = raw_transport()
    adapter, _ = make_adapter(transport)
    with pytest.raises(ProviderError) as info:
        adapter.create_response(REQ.model_copy(update={"extra_body": {"bad": {1, 2}}}))
    assert info.value.kind is ErrorKind.BAD_REQUEST and seen == []
    with pytest.raises(ProviderError):
        adapter.create_response(REQ.model_copy(update={"extra_body": {"bad": float("nan")}}))


# --- x-request-id ---------------------------------------------------------------------------


def test_missing_request_id_is_none_everywhere() -> None:
    body = {"status": "completed", "model": "qwen-3.8-27b", "output": []}
    transport, _ = raw_transport(body=json.dumps(body).encode())
    adapter, _ = make_adapter(transport)
    assert adapter.create_response(REQ).request_id is None
    transport, _ = raw_transport(status=503, body=b"{}")
    adapter, _ = make_adapter(transport, max_attempts=1)
    with pytest.raises(ProviderError) as info:
        adapter.create_response(REQ)
    assert info.value.request_id is None and "x-request-id" not in info.value.message


def test_request_id_captured_on_success_and_error() -> None:
    bridge = mock_transport(steps(A.OK_TEXT, (A.HTTP_ERROR, {"status": 400})))
    adapter, _ = make_adapter(bridge.transport)
    assert adapter.create_response(REQ).request_id == "fc_mock_1"
    with pytest.raises(ProviderError) as info:
        adapter.create_response(REQ)
    assert info.value.request_id == "fc_mock_2"
    assert "(x-request-id: fc_mock_2)" in info.value.message


# --- Secrets ------------------------------------------------------------------------------


def echo_transport(statuses: list[int]) -> tuple[httpx2.MockTransport, list[httpx2.Request]]:
    """Answers with each status in turn, echoing the planted key verbatim (the mock would
    redact it itself, ADR-027, so it cannot test the adapter's own redaction)."""
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        status = statuses[min(len(seen), len(statuses)) - 1]
        body = {"error": {"message": f"invalid key {LIVE_KEY} rejected", "code": LIVE_KEY}}
        return httpx2.Response(
            status, json=body, headers={"x-request-id": f"rid-{LIVE_KEY}", "retry-after": "1"}
        )

    return httpx2.MockTransport(handler), seen


def test_planted_key_never_appears_in_errors_logs_or_reprs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="flightcheck")
    register_secret(LIVE_KEY)  # what load_config does for a real key
    transport, seen = echo_transport([503, 503, 503, 401])
    adapter, sleeps = make_adapter(transport, api_key=LIVE_KEY)
    errors = []
    for _ in range(2):
        with pytest.raises(ProviderError) as info:
            adapter.create_response(REQ)
        errors.append(info.value)
    assert [e.kind for e in errors] == [ErrorKind.BUSY, ErrorKind.AUTH]
    assert len(sleeps) == 2 and caplog.records  # the retries were logged
    texts = [str(e) for e in errors] + [repr(e) for e in errors] + [e.message for e in errors]
    texts += [str(e.request_id) for e in errors] + [str(e.hint) for e in errors]
    texts += [str(e.__cause__) for e in errors]
    texts += [r.getMessage() for r in caplog.records] + [caplog.text]
    texts += [repr(adapter), repr(adapter.config), str(adapter.config.model_dump())]
    texts += [adapter.config.model_dump_json(), repr(adapter.profile)]
    for text in texts:
        assert LIVE_KEY not in text
        assert "Pl4ntedAdapterKey" not in text
    assert "invalid key [REDACTED] rejected" in errors[1].message
    # The key did reach the server, in the Authorization header only.
    assert seen[0].headers["authorization"] == f"Bearer {LIVE_KEY}"


def test_planted_key_in_success_logs_is_absent(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="flightcheck")
    bridge = mock_transport(steps(A.OK_TEXT))
    adapter, _ = make_adapter(bridge.transport, api_key=LIVE_KEY)
    adapter.create_response(REQ.model_copy(update={"max_output_tokens": 99_999, "model": "m-x"}))
    assert caplog.records  # clamp and catalogue warnings
    assert LIVE_KEY not in caplog.text
    assert bridge.recorder.list()[0].response_summary["auth"] == "other_key"


def test_planted_key_without_registration_is_still_redacted() -> None:
    echo = f"key {LIVE_KEY} unknown"
    error = error_from_response(401, httpx2.Headers(), json.dumps({"error": {"message": echo}}))
    assert LIVE_KEY not in error.message and "bc_live_…[REDACTED]" in error.message


def test_planted_key_in_a_stream_failure_is_redacted(caplog: pytest.LogCaptureFixture) -> None:
    body = json.dumps(
        {"type": "response.failed", "response": {"error": {"message": f"oops {LIVE_KEY}"}}}
    )
    transport, _ = raw_transport(
        body=f"event: response.failed\ndata: {body}\n\n".encode(),
        headers={"content-type": "text/event-stream"},
    )
    register_secret(LIVE_KEY)
    adapter, _ = make_adapter(transport, api_key=LIVE_KEY)
    events = []
    with pytest.raises(ProviderError) as info:
        events.extend(adapter.stream_response(REQ))
    assert info.value.kind is ErrorKind.STREAM_FAILED
    assert LIVE_KEY not in info.value.message


def test_planted_key_is_redacted_from_normalised_raw_body(caplog: pytest.LogCaptureFixture) -> None:
    body = {"status": "completed", "output": [], "echo": LIVE_KEY}
    transport, _ = raw_transport(body=json.dumps(body).encode())
    register_secret(LIVE_KEY)
    adapter, _ = make_adapter(transport, api_key=LIVE_KEY)
    result = adapter.create_response(REQ)
    assert LIVE_KEY not in json.dumps(result.raw)
    assert LIVE_KEY not in result.model_dump_json()


def test_missing_key_is_a_config_error_after_the_network_check() -> None:
    from flightcheck.core.errors import ConfigError
    from flightcheck.providers.bharatcode import BharatCodeAdapter

    transport, _ = raw_transport()
    with pytest.raises(ConfigError, match="BHARATCODE_API_KEY"):
        BharatCodeAdapter(make_config(api_key=None), transport=transport)
    with pytest.raises(ConfigError):
        BharatCodeAdapter(make_config(api_key="   "), transport=transport)


# --- Every adapter error is a ProviderError --------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        httpx2.ConnectError("x"),
        httpx2.ReadTimeout("x"),
        httpx2.RemoteProtocolError("x"),
        httpx2.DecodingError("x"),
        httpx2.InvalidURL("x"),
    ],
)
@pytest.mark.parametrize("operation", ["list", "responses", "chat", "stream", "chat_stream", "raw"])
def test_transport_failures_surface_as_provider_errors(exc: Exception, operation: str) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise exc

    adapter, _ = make_adapter(httpx2.MockTransport(handler), max_attempts=2)
    calls = {
        "list": adapter.list_models,
        "responses": lambda: adapter.create_response(REQ),
        "chat": lambda: adapter.create_chat_completion(CHAT),
        "stream": lambda: list(adapter.stream_response(REQ)),
        "chat_stream": lambda: list(adapter.stream_chat_completion(CHAT)),
        "raw": lambda: adapter.raw_post("responses", {"model": "qwen-3.8-27b", "input": "x"}),
    }
    with pytest.raises(ProviderError):
        calls[operation]()
