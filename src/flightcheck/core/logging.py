"""Logging that never leaks secrets. All FlightCheck logs go to stderr."""

from __future__ import annotations

import logging

from flightcheck.core.redact import redact

ROOT_LOGGER_NAME = "flightcheck"
_LOG_FORMAT = "%(levelname)s %(name)s: %(message)s"


class RedactingFilter(logging.Filter):
    """Redacts the fully formatted message (``msg % args``) and any traceback text."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True


class _FlightCheckHandler(logging.StreamHandler):
    """Marker subclass so configure_logging can replace its own handler idempotently."""


def get_logger(name: str | None = None) -> logging.Logger:
    """Return the ``flightcheck`` logger or a child of it (``flightcheck.<name>``)."""
    if not name or name == ROOT_LOGGER_NAME:
        return logging.getLogger(ROOT_LOGGER_NAME)
    if name.startswith(ROOT_LOGGER_NAME + "."):
        return logging.getLogger(name)
    return logging.getLogger(f"{ROOT_LOGGER_NAME}.{name}")


def _level_for(verbosity: int) -> int:
    if verbosity < 0:
        return logging.ERROR
    if verbosity == 0:
        return logging.WARNING
    if verbosity == 1:
        return logging.INFO
    return logging.DEBUG


def configure_logging(verbosity: int = 0) -> logging.Logger:
    """Attach one redacting stderr handler to the ``flightcheck`` logger.

    ``verbosity``: -1 quiet (errors only), 0 warnings, 1 info (``-v``), 2+ debug (``-vv``).
    Calling it again replaces the handler instead of adding a second one.
    """
    logger = logging.getLogger(ROOT_LOGGER_NAME)
    for handler in list(logger.handlers):
        if isinstance(handler, _FlightCheckHandler):
            logger.removeHandler(handler)
    handler = _FlightCheckHandler()  # defaults to sys.stderr at construction time
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    # On the handler, not the logger: logger filters do not see records from child loggers.
    handler.addFilter(RedactingFilter())
    logger.addHandler(handler)
    logger.setLevel(_level_for(verbosity))
    logger.propagate = False
    return logger
