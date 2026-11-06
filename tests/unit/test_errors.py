import pytest

from flightcheck.core.errors import (
    ConfigError,
    ConsentRequiredError,
    ErrorKind,
    FlightCheckError,
    NetworkDisabledError,
    PathOutsideRootError,
    ProviderError,
    UnsupportedCapabilityError,
)


def test_base_error_carries_message_hint_and_exit_code() -> None:
    error = FlightCheckError("Something broke", hint="Re-run with -v")
    assert str(error) == "Something broke"
    assert error.hint == "Re-run with -v"
    assert error.exit_code == 3


@pytest.mark.parametrize(
    "cls",
    [
        ConfigError,
        ConsentRequiredError,
        PathOutsideRootError,
        NetworkDisabledError,
        UnsupportedCapabilityError,
    ],
)
def test_usage_errors_exit_2(cls: type[FlightCheckError]) -> None:
    error = cls("bad")
    assert isinstance(error, FlightCheckError)
    assert error.exit_code == 2
    assert error.hint is None


def test_provider_error_fields() -> None:
    error = ProviderError(
        ErrorKind.BUSY, "model busy", http_status=503, request_id="req_123", retryable=True
    )
    assert error.exit_code == 3
    assert error.kind is ErrorKind.BUSY
    assert (error.http_status, error.request_id, error.retryable) == (503, "req_123", True)


def test_provider_error_message_is_redacted_and_truncated() -> None:
    error = ProviderError(ErrorKind.AUTH, "bad key bc_live_abcdef123456 " + "x" * 1000)
    assert "abcdef123456" not in error.message
    assert len(error.message) == 500
    assert error.message.endswith("…")
