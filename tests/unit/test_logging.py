import logging

import pytest

from flightcheck.core.logging import (
    ROOT_LOGGER_NAME,
    RedactingFilter,
    configure_logging,
    get_logger,
)


@pytest.fixture(autouse=True)
def _reset_flightcheck_logger():
    yield
    logger = logging.getLogger(ROOT_LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    logger.setLevel(logging.NOTSET)
    logger.propagate = True


def test_filter_redacts_percent_s_args_on_child_logger(capsys: pytest.CaptureFixture) -> None:
    configure_logging(verbosity=1)
    get_logger("scanner").info("calling with key %s and %r", "bc_live_abcdef123456", "x")
    err = capsys.readouterr().err
    assert "bc_live_…[REDACTED]" in err
    assert "abcdef123456" not in err
    assert "flightcheck.scanner" in err


def test_filter_redacts_message_text(capsys: pytest.CaptureFixture) -> None:
    configure_logging(verbosity=0)
    get_logger().warning("header Authorization: Bearer abcdefghijkl")
    err = capsys.readouterr().err
    assert "Bearer [REDACTED]" in err
    assert "abcdefghijkl" not in err


def test_filter_redacts_tracebacks(capsys: pytest.CaptureFixture) -> None:
    configure_logging(verbosity=0)
    try:
        raise RuntimeError("failed with api_key=supersecretvalue")
    except RuntimeError:
        get_logger("x").exception("boom")
    err = capsys.readouterr().err
    assert "supersecretvalue" not in err
    assert "api_key=[REDACTED]" in err


def test_logs_go_to_stderr_not_stdout(capsys: pytest.CaptureFixture) -> None:
    configure_logging(verbosity=0)
    get_logger().warning("hello")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "hello" in captured.err


@pytest.mark.parametrize(
    ("verbosity", "level"),
    [(-1, logging.ERROR), (0, logging.WARNING), (1, logging.INFO), (2, logging.DEBUG)],
)
def test_verbosity_levels(verbosity: int, level: int) -> None:
    assert configure_logging(verbosity).level == level


def test_configure_logging_is_idempotent() -> None:
    configure_logging(0)
    logger = configure_logging(2)
    assert len(logger.handlers) == 1


def test_get_logger_names() -> None:
    assert get_logger().name == "flightcheck"
    assert get_logger("core").name == "flightcheck.core"
    assert get_logger("flightcheck.mock").name == "flightcheck.mock"


def test_filter_handles_non_string_args() -> None:
    record = logging.LogRecord(
        "flightcheck", logging.INFO, __file__, 1, "data %s", ({"k": "sk-abcdefghijklmnop"},), None
    )
    assert RedactingFilter().filter(record)
    assert record.getMessage() == "data {'k': 'sk-…[REDACTED]'}"
