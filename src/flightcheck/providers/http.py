"""The one place FlightCheck creates HTTP clients (ADR-004), plus error mapping and retries.

- :func:`build_client` refuses to build a real client under ``FLIGHTCHECK_NO_NETWORK``; tests
  inject an ``httpx2`` transport instead (ADR-016). The API key goes only into the
  ``Authorization`` header.
- :func:`error_from_response` maps an HTTP error to :class:`ProviderError`: status code first,
  body hints second (plan §14.4).
- :func:`send_with_retry` runs an operation under a :class:`RetryPolicy` (plan §14.5) with an
  injected ``sleep``.
"""

from __future__ import annotations

import json
import os
import random
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, TypeVar

import httpx2
from pydantic import SecretStr

from flightcheck import __version__
from flightcheck.core.config import RetryPolicy, TimeoutConfig
from flightcheck.core.errors import ErrorKind, NetworkDisabledError, ProviderError
from flightcheck.core.logging import get_logger
from flightcheck.core.redact import redact

__all__ = [
    "NO_NETWORK_ENV",
    "HTTPX_ERRORS",
    "REQUEST_ID_HEADER",
    "build_client",
    "error_from_response",
    "error_from_transport",
    "kind_for_status",
    "network_disabled",
    "parse_json_body",
    "parse_retry_after",
    "request_id_of",
    "retry_after_of",
    "send_with_retry",
]

NO_NETWORK_ENV = "FLIGHTCHECK_NO_NETWORK"
REQUEST_ID_HEADER = "x-request-id"
MODEL_NOT_IN_PLAN = "model_not_in_plan"
_OFF_VALUES = frozenset({"", "0", "false", "no", "off"})
_BODY_PREVIEW = 500

_log = get_logger("providers.http")
T = TypeVar("T")


def network_disabled(environ: dict[str, str] | None = None) -> bool:
    """True when ``FLIGHTCHECK_NO_NETWORK`` is set to anything but an "off" value.

    Fails closed: ``1``, ``true``, ``yes`` and any other non-empty value disable the network.
    """
    env = os.environ if environ is None else environ
    return env.get(NO_NETWORK_ENV, "").strip().lower() not in _OFF_VALUES


def build_client(
    base_url: str,
    api_key: SecretStr | str | None,
    timeout: TimeoutConfig,
    transport: httpx2.BaseTransport | None = None,
) -> httpx2.Client:
    """An ``httpx2.Client`` for ``base_url``.

    Raises :class:`NetworkDisabledError` when ``FLIGHTCHECK_NO_NETWORK`` is on and no
    ``transport`` is injected. Redirects are not followed, so the key is sent only to
    ``base_url``'s host.
    """
    if transport is None and network_disabled():
        raise NetworkDisabledError(
            f"Network access is disabled ({NO_NETWORK_ENV}=1): no provider client was created.",
            hint=f"Unset {NO_NETWORK_ENV} to call the provider, or inject a test transport.",
        )
    headers = {"accept": "application/json", "user-agent": f"flightcheck/{__version__}"}
    key = api_key.get_secret_value() if isinstance(api_key, SecretStr) else api_key
    if key:
        headers["authorization"] = f"Bearer {key}"
    return httpx2.Client(
        base_url=base_url.rstrip("/") + "/",
        headers=headers,
        timeout=httpx2.Timeout(
            connect=timeout.connect_s,
            read=timeout.read_s,
            write=timeout.write_s,
            pool=timeout.pool_s,
        ),
        follow_redirects=False,
        transport=transport,
    )


# --- Responses → errors ---------------------------------------------------------------------


def request_id_of(response: httpx2.Response) -> str | None:
    """The ``x-request-id`` header (BharatCode asks for it in problem reports), if any."""
    value = response.headers.get(REQUEST_ID_HEADER)
    return redact(value) if value else None


def kind_for_status(status: int) -> ErrorKind:
    """Status code → :class:`ErrorKind` (plan §14.4)."""
    if status == 400:
        return ErrorKind.BAD_REQUEST
    if status == 401:
        return ErrorKind.AUTH
    if status == 403:
        return ErrorKind.FORBIDDEN
    if status == 404:
        return ErrorKind.NOT_FOUND
    if status == 422:
        return ErrorKind.UNPROCESSABLE
    if status == 429:
        return ErrorKind.RATE_LIMITED  # [UNVERIFIED] for BharatCode
    if status == 503:
        return ErrorKind.BUSY  # documented: "model is busy, wait a few seconds and retry"
    if status >= 500:
        return ErrorKind.SERVER
    if status >= 400:
        return ErrorKind.BAD_REQUEST  # any other 4xx: a client-side problem, never retried
    return ErrorKind.MALFORMED_RESPONSE  # 1xx/3xx: not an answer an API client can use


_RETRYABLE_KINDS = frozenset({ErrorKind.BUSY, ErrorKind.RATE_LIMITED})


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    """Seconds from a ``Retry-After`` header (delta-seconds or HTTP date); None if absent or
    unparseable. Whether BharatCode sends it is [UNVERIFIED]."""
    if not value:
        return None
    value = value.strip()
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = (when - (now or datetime.now(UTC))).total_seconds()
    if seconds != seconds or seconds in (float("inf"), float("-inf")):  # NaN / inf
        return None
    return max(0.0, seconds)


def retry_after_of(error: ProviderError) -> float | None:
    """The ``Retry-After`` delay attached by :func:`error_from_response`, if any."""
    value = getattr(error, "retry_after_s", None)
    return value if isinstance(value, float) else None


def _error_details(body_text: str) -> tuple[str | None, str | None]:
    """(message, code) from an OpenAI-style ``{"error": {...}}`` body; the shape is
    [UNVERIFIED] for BharatCode, so anything else gives (None, None)."""
    try:
        body = json.loads(body_text)
    except ValueError:
        return None, None
    if not isinstance(body, dict):
        return None, None
    error = body.get("error")
    if isinstance(error, str):
        return error, None
    if not isinstance(error, dict):
        message = body.get("message")
        return (message if isinstance(message, str) else None), None
    message = error.get("message")
    code = error.get("code")
    return (
        message if isinstance(message, str) else None,
        code if isinstance(code, str) else None,
    )


def _with_request_id(message: str, request_id: str | None) -> str:
    return f"{message} (x-request-id: {request_id})" if request_id else message


def error_from_response(
    status: int, headers: httpx2.Headers, body_text: str, *, provider: str = "provider"
) -> ProviderError:
    """Build a :class:`ProviderError` for an HTTP error answer.

    Status first; then body hints: ``model_not_in_plan`` anywhere in the body means
    ``FORBIDDEN`` whatever the status ([OBSERVED] in BharatCode desktop release notes; confirm
    in Phase 10). The message is redacted and truncated by :class:`ProviderError`.
    """
    request_id = headers.get(REQUEST_ID_HEADER)
    request_id = redact(request_id) if request_id else None
    kind = kind_for_status(status)
    if MODEL_NOT_IN_PLAN in body_text:
        kind = ErrorKind.FORBIDDEN
    message, code = _error_details(body_text)
    if message is None:
        preview = body_text.strip()[:_BODY_PREVIEW]
        message = preview or "(empty body)"
    label = f"{provider} answered HTTP {status} ({kind.value})"
    if code:
        label += f" [{code}]"
    text = _with_request_id(f"{label}: {message}", request_id)
    retry_after = parse_retry_after(headers.get("retry-after"))
    error = ProviderError(
        kind,
        text,
        http_status=status,
        request_id=request_id,
        retryable=kind in _RETRYABLE_KINDS,
    )
    error.retry_after_s = retry_after  # type: ignore[attr-defined]
    return error


# Every exception httpx2 raises while building, sending or reading a request.
HTTPX_ERRORS: tuple[type[Exception], ...] = (
    httpx2.HTTPError,
    httpx2.InvalidURL,
    httpx2.StreamError,
)


def error_from_transport(exc: Exception, *, provider: str = "provider") -> ProviderError:
    """Map an ``httpx2`` exception without an HTTP answer to :class:`ProviderError`.

    Timeouts → ``TIMEOUT`` (not retried: a queued request may still be running). Network and
    protocol failures → ``CONNECTION`` (retryable). An invalid URL or unsupported scheme →
    ``CONNECTION``, not retryable (retrying cannot fix it). Undecodable content →
    ``MALFORMED_RESPONSE``. Only the exception type is shown, never its text, which can
    contain the URL.
    """
    name = type(exc).__name__
    if isinstance(exc, httpx2.TimeoutException):
        return ProviderError(
            ErrorKind.TIMEOUT,
            f"No answer from {provider} in time ({name}).",
            hint="Requests can queue on BharatCode; raise providers.<name>.timeout.read_s.",
        )
    if isinstance(exc, httpx2.UnsupportedProtocol | httpx2.InvalidURL):
        return ProviderError(
            ErrorKind.CONNECTION, f"Cannot reach {provider}: invalid URL or scheme ({name})."
        )
    if isinstance(exc, httpx2.DecodingError):
        return ProviderError(
            ErrorKind.MALFORMED_RESPONSE, f"Cannot decode the answer from {provider} ({name})."
        )
    if isinstance(exc, httpx2.TransportError):
        return ProviderError(
            ErrorKind.CONNECTION, f"Cannot reach {provider} ({name}).", retryable=True
        )
    return ProviderError(ErrorKind.CONNECTION, f"Request to {provider} failed ({name}).")


def parse_json_body(response: httpx2.Response, *, provider: str = "provider") -> dict[str, Any]:
    """The response body as a JSON object, or ``ProviderError(MALFORMED_RESPONSE)`` with the
    redacted first 500 characters (plan §20.11)."""
    request_id = request_id_of(response)
    text = response.text
    try:
        body = json.loads(text)
    except ValueError:
        body = None
        problem = "is not valid JSON"
    else:
        problem = "is not a JSON object"
    if not isinstance(body, dict):
        preview = text.strip()[:_BODY_PREVIEW] or "(empty body)"
        raise ProviderError(
            ErrorKind.MALFORMED_RESPONSE,
            _with_request_id(
                f"{provider} answered HTTP {response.status_code} but the body {problem}: "
                f"{preview}",
                request_id,
            ),
            http_status=response.status_code,
            request_id=request_id,
        )
    return body


# --- Retries --------------------------------------------------------------------------------


def _default_jitter() -> float:
    return random.uniform(0.8, 1.2)


def send_with_retry(
    fn: Callable[[], T],
    policy: RetryPolicy,
    sleep: Callable[[float], None],
    *,
    jitter: Callable[[], float] = _default_jitter,
    attempts: list[dict[str, Any]] | None = None,
) -> T:
    """Call ``fn`` until it returns, retrying bounded and visibly (plan §14.5).

    Retries only a :class:`ProviderError` that is ``retryable`` and whose kind is in
    ``policy.retry_on`` (by default 503 busy, 429 and connection failures): never another 4xx,
    a timeout, or a non-error result such as a truncated response (callers return those).
    Delay = ``min(max_delay, base * 2**(attempt-1)) * jitter()``; a ``Retry-After`` header
    ([UNVERIFIED] for BharatCode) is honoured: the wait is at least that long, and when it is
    longer than ``max_delay_s`` the error is raised instead of waiting. Each attempt is
    appended to ``attempts`` when given. Anything other than a ``ProviderError`` propagates
    unchanged; callers convert transport errors first.
    """
    record = attempts if attempts is not None else []
    for attempt in range(1, policy.max_attempts + 1):
        try:
            result = fn()
        except ProviderError as error:
            entry: dict[str, Any] = {
                "attempt": attempt,
                "outcome": error.kind.value,
                "http_status": error.http_status,
                "request_id": error.request_id,
            }
            record.append(entry)
            if not (error.retryable and error.kind in policy.retry_on):
                raise
            if attempt == policy.max_attempts:
                raise _gave_up(error, attempt) from error
            delay = policy.delay_for(attempt, jitter())
            retry_after = retry_after_of(error)
            if retry_after is not None:
                if retry_after > policy.max_delay_s:
                    entry["retry_after_s"] = retry_after
                    raise _gave_up(
                        error, attempt, f"Retry-After {retry_after:g} s exceeds the limit"
                    ) from error
                delay = max(delay, retry_after)
            entry["delay_s"] = round(delay, 3)
            _log.warning(
                "%s; retrying in %.1f s (attempt %d of %d).",
                error.message,
                delay,
                attempt + 1,
                policy.max_attempts,
            )
            sleep(delay)
        else:
            record.append({"attempt": attempt, "outcome": "ok"})
            return result
    raise AssertionError("unreachable: max_attempts >= 1")  # pragma: no cover


def _gave_up(error: ProviderError, attempt: int, why: str | None = None) -> ProviderError:
    suffix = f"gave up after {attempt} attempt{'s' if attempt != 1 else ''}"
    if why:
        suffix = f"{why}; {suffix}"
    final = ProviderError(
        error.kind,
        f"{error.message} ({suffix})",
        http_status=error.http_status,
        request_id=error.request_id,
        retryable=error.retryable,
        hint=error.hint,
    )
    final.retry_after_s = retry_after_of(error)  # type: ignore[attr-defined]
    return final
