"""Exception hierarchy with user-facing messages, hints and CLI exit codes.

Exit codes (docs/ARCHITECTURE.md §7): 2 = usage, configuration or consent error;
3 = internal or infrastructure error.
"""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar

from flightcheck.core.redact import redact

PROVIDER_MESSAGE_LIMIT = 500


class FlightCheckError(Exception):
    """Base class for every error FlightCheck reports to the user."""

    exit_code: ClassVar[int] = 3

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:
        return self.message


class ConfigError(FlightCheckError):
    """Invalid or unsafe configuration (bad flightcheck.toml, bad flag value)."""

    exit_code: ClassVar[int] = 2


class ConsentRequiredError(FlightCheckError):
    """An action needs explicit consent (--allow-exec, --yes) that was not given."""

    exit_code: ClassVar[int] = 2


class PathOutsideRootError(FlightCheckError):
    """A path resolves outside the directory it must stay in."""

    exit_code: ClassVar[int] = 2


class NetworkDisabledError(FlightCheckError):
    """A network client was requested while FLIGHTCHECK_NO_NETWORK=1."""

    exit_code: ClassVar[int] = 2


class UnsupportedCapabilityError(FlightCheckError):
    """The selected provider does not support a requested capability."""

    exit_code: ClassVar[int] = 2


class ErrorKind(StrEnum):
    """Normalised provider error categories (status code first, body hints second)."""

    AUTH = "auth"  # 401
    FORBIDDEN = "forbidden"  # 403 (e.g. model not in plan; status code [UNVERIFIED])
    NOT_FOUND = "not_found"  # 404
    BAD_REQUEST = "bad_request"  # 400, includes documented unsupported features
    UNPROCESSABLE = "unprocessable"  # 422
    RATE_LIMITED = "rate_limited"  # 429, [UNVERIFIED] for BharatCode
    BUSY = "busy"  # 503, documented "model is busy"
    SERVER = "server"  # other 5xx
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    MALFORMED_RESPONSE = "malformed_response"
    STREAM_FAILED = "stream_failed"  # response.failed during a started stream
    UNSUPPORTED_CAPABILITY = "unsupported_capability"


class ProviderError(FlightCheckError):
    """A normalised error from a model provider.

    The stored message is redacted and truncated to 500 characters.
    """

    exit_code: ClassVar[int] = 3

    def __init__(
        self,
        kind: ErrorKind,
        message: str,
        *,
        http_status: int | None = None,
        request_id: str | None = None,
        retryable: bool = False,
        hint: str | None = None,
    ) -> None:
        safe = redact(message)
        if len(safe) > PROVIDER_MESSAGE_LIMIT:
            safe = safe[: PROVIDER_MESSAGE_LIMIT - 1] + "…"
        super().__init__(safe, hint)
        self.kind = kind
        self.http_status = http_status
        self.request_id = request_id
        self.retryable = retryable
