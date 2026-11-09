"""CLI presentation helpers: option choices, consoles, the exit line and error messages."""

from __future__ import annotations

import os
import shlex
import traceback
from collections.abc import Sequence
from enum import StrEnum

from rich.console import Console
from rich.panel import Panel
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


def render_exit_line(
    console: Console, code: int, fail_on: FailOn, target: str, *, command: str = "scan"
) -> None:
    if fail_on is FailOn.NONE:
        reason = "--fail-on none"
    elif code == 1:
        reason = f"findings ≥ {fail_on.value}"
    else:
        reason = f"no findings ≥ {fail_on.value}"
    rerun = f"flightcheck {command} {target}" + (" --allow-exec" if command == "test" else "")
    hints = [f"fix the findings above and re-run {rerun}"] if code else []
    if command == "scan":  # ADR-025 item 5: hints name only commands that exist
        hints.append(f"flightcheck test {target} --allow-exec")
    hint = f" Next: {' · '.join(hints)}" if hints else ""
    console.print(Text(f"Exit {code} ({reason}).{hint}"), soft_wrap=True)


def render_exec_warning(
    console: Console,
    *,
    command: Sequence[str],
    cwd: str,
    scenarios: Sequence[tuple[str, str]],
    timeout_s: int,
    refusal: str | None = None,
) -> None:
    """The execution warning panel (§23.2, §27 Phase 7B item 17): always shown before the
    app runs, after the pre-flight. Everything in it is redacted."""
    names = ", ".join(f"{sid} {name}" for sid, name in scenarios) or "none"
    first = (
        f"Pre-flight refused: {refusal}. The command will NOT run; every check is skipped."
        if refusal
        else "Your code will run: FlightCheck starts this command once per scenario."
    )
    lines = [
        first,
        f"Command:   {shlex.join(command)}",
        f"Directory: {cwd}",
        f"Scenarios: {names}",
        f"Timeout:   {timeout_s} s per scenario",
        "Model traffic goes to a local mock on 127.0.0.1 with a fake API key. The egress guard "
        "is best effort, not a sandbox: the command can still read files and open sockets.",
    ]
    console.print(
        Panel(
            Text(redact("\n".join(lines))),
            title="Execution warning",
            title_align="left",
            border_style="yellow",
            expand=False,
        )
    )


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
    "render_exec_warning",
    "render_exit_line",
    "render_internal_error",
]
