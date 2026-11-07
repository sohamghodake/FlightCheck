"""providers/http.py: client construction, the no-network switch and retries (plan §14.5)."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx2
import pytest
from provider_bridge import (
    FAKE_KEY,
    make_adapter,
    make_config,
    mock_transport,
    raw_transport,
    steps,
)
from pydantic import SecretStr

from flightcheck.core.config import RetryPolicy, TimeoutConfig
from flightcheck.core.errors import ErrorKind, NetworkDisabledError, ProviderError
from flightcheck.mock.faults import MockAction as A
from flightcheck.providers.base import ChatRequest, FinishState, ResponsesRequest
from flightcheck.providers.bharatcode import BharatCodeAdapter
from flightcheck.providers.http import (
    build_client,
    network_disabled,
    parse_retry_after,
    send_with_retry,
)

REQ = ResponsesRequest(model="qwen-3.8-27b", input="hi")
CHAT = ChatRequest(model="qwen-3.8-27b", messages=[{"role": "user", "content": "hi"}])


# --- build_client and the no-network switch -------------------------------------------------


def test_no_network_switch_is_on_in_tests() -> None:
    assert network_disabled() is True


@pytest.mark.parametrize(
    ("value", "disabled"),
    [("1", True), ("true", True), ("yes", True), ("anything", True), ("0", False), ("", False)],
)
def test_network_disabled_fails_closed(value: str, disabled: bool) -> None:
    assert network_disabled({"FLIGHTCHECK_NO_NETWORK": value}) is disabled
    assert network_disabled({}) is False


def test_build_client_without_transport_is_refused() -> None:
    with pytest.raises(NetworkDisabledError, match="FLIGHTCHECK_NO_NETWORK"):
        build_client("https://bharatcode.ai/api/model/v1", "k", TimeoutConfig())


def test_default_adapter_without_transport_raises_network_disabled() -> None:
    """Acceptance (plan §27 Phase 6 item 18)."""
    with pytest.raises(NetworkDisabledError):
        BharatCodeAdapter(make_config())
    with pytest.raises(NetworkDisabledError):  # even with no key configured
        BharatCodeAdapter(make_config(api_key=None))


def test_injected_transport_works_under_the_switch() -> None:
    transport, seen = raw_transport(body=b'{"ok": true}')
    client = build_client(
        "https://bharatcode.ai/api/model/v1/", SecretStr("k-123"), TimeoutConfig(), transport
    )
    with client:
        assert client.get("models").json() == {"ok": True}
    assert str(seen[0].url) == "https://bharatcode.ai/api/model/v1/models"


def test_key_goes_only_into_the_authorization_header() -> None:
    transport, seen = raw_transport(body=b'{"data": []}')
    adapter, _ = make_adapter(transport, api_key="bc_live_Pl4ntedHeaderKey99")
    adapter.list_models()
    request = seen[0]
    assert request.headers["authorization"] == "Bearer bc_live_Pl4ntedHeaderKey99"
    others = [v for k, v in request.headers.items() if k.lower() != "authorization"]
    assert all("Pl4nted" not in v for v in others)
    assert "Pl4nted" not in str(request.url)
    assert request.content == b""


def test_timeouts_come_from_config() -> None:
    transport, _ = raw_transport()
    timeout = TimeoutConfig(connect_s=1, read_s=2, write_s=3, pool_s=4)
    client = build_client("https://x.test/v1", None, timeout, transport)
    assert (
        client.timeout.connect,
        client.timeout.read,
        client.timeout.write,
        client.timeout.pool,
    ) == (
        1,
        2,
        3,
        4,
    )
    assert "authorization" not in client.headers
    assert client.follow_redirects is False


# --- send_with_retry (unit) -----------------------------------------------------------------


def failing(*errors: ProviderError, result: str = "ok"):  # noqa: ANN201
    queue = list(errors)
    calls: list[int] = []

    def fn() -> str:
        calls.append(1)
        if queue:
            raise queue.pop(0)
        return result

    return fn, calls


def busy(retry_after: float | None = None) -> ProviderError:
    error = ProviderError(ErrorKind.BUSY, "busy", http_status=503, retryable=True)
    error.retry_after_s = retry_after  # type: ignore[attr-defined]
    return error


def test_retry_twice_then_success_with_increasing_sleeps() -> None:
    fn, calls = failing(busy(), busy())
    sleeps: list[float] = []
    attempts: list[dict] = []
    assert send_with_retry(fn, RetryPolicy(), sleeps.append, attempts=attempts) == "ok"
    assert len(calls) == 3
    assert len(sleeps) == 2 and sleeps[0] < sleeps[1]
    assert [a["outcome"] for a in attempts] == ["busy", "busy", "ok"]


def test_backoff_values_follow_the_formula() -> None:
    fn, _ = failing(busy(), busy(), busy())
    sleeps: list[float] = []
    policy = RetryPolicy(max_attempts=4, base_delay_s=2, max_delay_s=5)
    send_with_retry(fn, policy, sleeps.append, jitter=lambda: 1.0)
    assert sleeps == [2.0, 4.0, 5.0]  # capped at max_delay_s


def test_default_jitter_stays_within_bounds() -> None:
    fn, _ = failing(*(busy() for _ in range(5)))
    sleeps: list[float] = []
    send_with_retry(fn, RetryPolicy(max_attempts=6, max_delay_s=1000), sleeps.append)
    for attempt, delay in enumerate(sleeps, start=1):
        base = 2.0 * 2 ** (attempt - 1)
        assert 0.8 * base <= delay <= 1.2 * base


def test_gives_up_after_max_attempts() -> None:
    fn, calls = failing(busy(), busy(), busy(), busy())
    sleeps: list[float] = []
    with pytest.raises(ProviderError, match="gave up after 3 attempts") as info:
        send_with_retry(fn, RetryPolicy(), sleeps.append)
    assert info.value.kind is ErrorKind.BUSY and info.value.http_status == 503
    assert len(calls) == 3 and len(sleeps) == 2


@pytest.mark.parametrize(
    "error",
    [
        ProviderError(ErrorKind.BAD_REQUEST, "x", http_status=400),
        ProviderError(ErrorKind.AUTH, "x", http_status=401),
        ProviderError(ErrorKind.FORBIDDEN, "x", http_status=403),
        ProviderError(ErrorKind.SERVER, "x", http_status=500),
        ProviderError(ErrorKind.TIMEOUT, "x"),
        # retryable flag without the kind in retry_on is not enough
        ProviderError(ErrorKind.SERVER, "x", http_status=500, retryable=True),
    ],
)
def test_non_retryable_errors_are_raised_at_once(error: ProviderError) -> None:
    fn, calls = failing(error)
    sleeps: list[float] = []
    with pytest.raises(ProviderError) as info:
        send_with_retry(fn, RetryPolicy(), sleeps.append)
    assert info.value is error
    assert len(calls) == 1 and sleeps == []


def test_retry_on_respects_the_policy() -> None:
    fn, calls = failing(busy())
    with pytest.raises(ProviderError):
        send_with_retry(fn, RetryPolicy(retry_on=frozenset()), lambda _: None)
    assert len(calls) == 1


def test_single_attempt_policy_never_sleeps() -> None:
    fn, calls = failing(busy())
    sleeps: list[float] = []
    with pytest.raises(ProviderError, match="gave up after 1 attempt\\)"):
        send_with_retry(fn, RetryPolicy(max_attempts=1), sleeps.append)
    assert len(calls) == 1 and sleeps == []


def test_retry_after_is_honoured_as_a_minimum() -> None:
    fn, _ = failing(busy(retry_after=7.0))
    sleeps: list[float] = []
    send_with_retry(fn, RetryPolicy(), sleeps.append, jitter=lambda: 1.0)
    assert sleeps == [7.0]


def test_short_retry_after_does_not_shorten_backoff() -> None:
    fn, _ = failing(busy(retry_after=0.5))
    sleeps: list[float] = []
    send_with_retry(fn, RetryPolicy(), sleeps.append, jitter=lambda: 1.0)
    assert sleeps == [2.0]


def test_retry_after_beyond_the_limit_stops_retrying() -> None:
    fn, calls = failing(busy(retry_after=60.0))
    sleeps: list[float] = []
    with pytest.raises(ProviderError, match="Retry-After 60 s exceeds the limit"):
        send_with_retry(fn, RetryPolicy(max_delay_s=20), sleeps.append)
    assert len(calls) == 1 and sleeps == []


def test_other_exceptions_propagate_unchanged() -> None:
    def boom() -> None:
        raise RuntimeError("not a provider error")

    with pytest.raises(RuntimeError):
        send_with_retry(boom, RetryPolicy(), lambda _: None)


@pytest.mark.parametrize(
    ("value", "seconds"),
    [
        ("3", 3.0),
        (" 2.5 ", 2.5),
        ("-4", 0.0),
        (None, None),
        ("", None),
        ("soon", None),
        ("nan", None),
        ("inf", None),
        ("Sat, 07 Nov 2026 10:00:30 GMT", 30.0),
    ],
)
def test_parse_retry_after(value: str | None, seconds: float | None) -> None:
    now = datetime(2026, 11, 7, 10, 0, 0, tzinfo=UTC)
    assert parse_retry_after(value, now=now) == seconds


# --- Retries through the adapter and the mock ------------------------------------------------


def test_adapter_retries_503_twice_then_succeeds() -> None:
    bridge = mock_transport(steps(A.HTTP_ERROR, A.HTTP_ERROR, A.OK_TEXT))
    adapter, sleeps = make_adapter(bridge.transport)
    result = adapter.create_response(REQ)
    assert result.finish_state is FinishState.COMPLETE
    assert len(bridge.requests) == 3
    assert sleeps == [2.0, 4.0]
    attempts = result.raw["_flightcheck_attempts"]
    assert [a["outcome"] for a in attempts] == ["busy", "busy", "ok"]
    assert [a.get("request_id") for a in attempts[:2]] == ["fc_mock_1", "fc_mock_2"]
    assert result.request_id == "fc_mock_3"


def test_adapter_gives_up_on_persistent_503() -> None:
    bridge = mock_transport(steps(A.HTTP_ERROR, A.HTTP_ERROR, A.HTTP_ERROR, A.OK_TEXT))
    adapter, sleeps = make_adapter(bridge.transport)
    with pytest.raises(ProviderError) as info:
        adapter.create_response(REQ)
    assert info.value.kind is ErrorKind.BUSY and info.value.retryable
    assert info.value.request_id == "fc_mock_3"
    assert len(bridge.requests) == 3 and len(sleeps) == 2


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (400, ErrorKind.BAD_REQUEST),
        (401, ErrorKind.AUTH),
        (404, ErrorKind.NOT_FOUND),
        (500, ErrorKind.SERVER),
    ],
)
def test_adapter_does_not_retry_4xx_or_500(status: int, kind: ErrorKind) -> None:
    bridge = mock_transport(steps((A.HTTP_ERROR, {"status": status}), A.OK_TEXT))
    adapter, sleeps = make_adapter(bridge.transport)
    with pytest.raises(ProviderError) as info:
        adapter.create_response(REQ)
    assert info.value.kind is kind and info.value.http_status == status
    assert len(bridge.requests) == 1 and sleeps == []


def test_adapter_retries_429_and_honours_retry_after() -> None:
    bridge = mock_transport(steps((A.HTTP_ERROR, {"status": 429, "retry_after": 5}), A.OK_TEXT))
    adapter, sleeps = make_adapter(bridge.transport)
    assert adapter.create_response(REQ).finish_state is FinishState.COMPLETE
    assert sleeps == [5.0]


def test_adapter_never_retries_a_truncated_result() -> None:
    bridge = mock_transport(steps(A.TRUNCATE, A.OK_TEXT))
    adapter, sleeps = make_adapter(bridge.transport)
    result = adapter.create_chat_completion(CHAT)
    assert result.finish_state is FinishState.TRUNCATED
    assert len(bridge.requests) == 1 and sleeps == []
    assert [a["outcome"] for a in result.raw["_flightcheck_attempts"]] == ["ok"]


def test_missing_auth_is_401_and_not_retried() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(401, json={"error": {"message": "bad key"}})

    adapter, sleeps = make_adapter(httpx2.MockTransport(handler))
    with pytest.raises(ProviderError) as info:
        adapter.list_models()
    assert info.value.kind is ErrorKind.AUTH and sleeps == []


def test_connection_errors_are_retried() -> None:
    calls: list[int] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(1)
        if len(calls) < 3:
            raise httpx2.ConnectError("connection refused", request=request)
        return httpx2.Response(200, json={"data": [{"id": "qwen-3.8-27b"}]})

    adapter, sleeps = make_adapter(httpx2.MockTransport(handler))
    assert [m.id for m in adapter.list_models()] == ["qwen-3.8-27b"]
    assert len(calls) == 3 and sleeps == [2.0, 4.0]


def test_timeouts_are_not_retried() -> None:
    calls: list[int] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(1)
        raise httpx2.ReadTimeout("timed out", request=request)

    adapter, sleeps = make_adapter(httpx2.MockTransport(handler))
    with pytest.raises(ProviderError) as info:
        adapter.create_response(REQ)
    assert info.value.kind is ErrorKind.TIMEOUT and info.value.hint
    assert len(calls) == 1 and sleeps == []


def test_stream_is_retried_before_it_starts() -> None:
    bridge = mock_transport(steps(A.HTTP_ERROR, A.OK_TEXT))
    adapter, sleeps = make_adapter(bridge.transport)
    events = list(adapter.stream_response(REQ))
    assert events[-1].kind == "completed"
    assert len(bridge.requests) == 2 and sleeps == [2.0]


def test_stream_is_never_retried_once_started() -> None:
    bridge = mock_transport(steps(A.STREAM_FAIL_AFTER, A.OK_TEXT))
    adapter, sleeps = make_adapter(bridge.transport)
    seen = []
    with pytest.raises(ProviderError) as info:
        for event in adapter.stream_response(REQ):
            seen.append(event.kind)
    assert info.value.kind is ErrorKind.STREAM_FAILED and not info.value.retryable
    assert seen[-1] == "failed" and "text_delta" in seen
    assert len(bridge.requests) == 1 and sleeps == []


def test_connection_drop_mid_stream_is_not_retried() -> None:
    calls: list[int] = []

    def body():  # noqa: ANN202
        yield b'event: response.output_text.delta\ndata: {"delta": "a"}\n\n'
        raise httpx2.ReadError("connection reset")

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(1)
        return httpx2.Response(200, headers={"x-request-id": "rid-9"}, content=body())

    adapter, sleeps = make_adapter(httpx2.MockTransport(handler))
    seen = []
    with pytest.raises(ProviderError) as info:
        for event in adapter.stream_response(REQ):
            seen.append(event.kind)
    assert seen == ["text_delta"]
    assert info.value.kind is ErrorKind.CONNECTION and info.value.request_id == "rid-9"
    assert "interrupted" in info.value.message
    assert len(calls) == 1 and sleeps == []


def test_fake_key_constant_is_not_a_live_key() -> None:
    assert not FAKE_KEY.startswith("bc_live_")
