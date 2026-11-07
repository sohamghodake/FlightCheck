"""CLI presentation helpers: option choices, consoles, the exit line and error messages."""

from __future__ import annotations

import os
import traceback
from enum import StrEnum

from rich.console import Console
from rich.text import Text

from flightcheck.core.errors import FlightCheckError
from flightcheck.core.models import FindingKind, Report, Severity
from flightcheck.core.redact import redact


class OutputFormat(StrEnum):
    TERMINAL = "terminal"
    JSON = "json"
    HTML = "html"
    ALL = "all"


class ProviderChoice(StrEnum):
    AUTO = "auto"
    BHARATCODE = "bharatcode"
    OPENAI_COMPATIBLE = "openai_compatible"


class FailOn(StrEnum):
    NONE = "none"
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


def make_console(*, no_color: bool = False, stderr: bool = False) -> Console:
    """Rich console; ``--no-color`` or ``NO_COLOR`` turns colour off."""
    return Console(
        stderr=stderr,
        no_color=no_color or bool(os.environ.get("NO_COLOR")),
        highlight=False,
        emoji=False,
    )


def exceeds_fail_on(report: Report, fail_on: FailOn, *, include_hypotheses: bool) -> bool:
    """True when a finding is at or above ``fail_on`` (hypotheses only when asked)."""
    if fail_on is FailOn.NONE:
        return False
    threshold = Severity(fail_on.value).rank
    return any(
        f.severity.rank >= threshold
        for f in report.findings
        if include_hypotheses or f.kind is not FindingKind.HYPOTHESIS
    )


def render_exit_line(console: Console, code: int, fail_on: FailOn, target: str) -> None:
    if fail_on is FailOn.NONE:
        reason = "--fail-on none"
    elif code == 1:
        reason = f"findings ≥ {fail_on.value}"
    else:
        reason = f"no findings ≥ {fail_on.value}"
    hint = f" Next: fix the findings above and re-run flightcheck scan {target}" if code else ""
    console.print(Text(f"Exit {code} ({reason}).{hint}"), soft_wrap=True)


def render_error(console: Console, exc: FlightCheckError) -> None:
    console.print(Text("Error: ", style="bold red") + Text(redact(exc.message)), soft_wrap=True)
    if exc.hint:
        console.print(Text(redact(exc.hint), style="dim"), soft_wrap=True)


def render_internal_error(console: Console, exc: BaseException, *, verbose: bool) -> None:
    if verbose:
        console.print(Text(redact(traceback.format_exc()), style="dim"))
    console.print(
        Text("Internal error: ", style="bold red")
        + Text(redact(f"{type(exc).__name__}: {exc}"))
        + Text(" — re-run with -v for details."),
        soft_wrap=True,
    )


__all__ = [
    "FailOn",
    "OutputFormat",
    "ProviderChoice",
    "exceeds_fail_on",
    "make_console",
    "render_error",
    "render_exit_line",
    "render_internal_error",
]
