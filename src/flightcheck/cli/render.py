"""CLI presentation helpers: option choices, consoles, the exit line and error messages."""

from __future__ import annotations

import json
import os
import shlex
import sys
import traceback
from collections.abc import Sequence
from enum import StrEnum
from typing import Any, Protocol

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


class ReportFormat(StrEnum):
    """``flightcheck report --format`` (§23.2); markdown is P1 and exits 2."""

    HTML = "html"
    TERMINAL = "terminal"
    MARKDOWN = "markdown"


class ProviderChoice(StrEnum):
    AUTO = "auto"
    BHARATCODE = "bharatcode"
    OPENAI_COMPATIBLE = "openai_compatible"


class LLMChoice(StrEnum):
    NONE = "none"
    FAKE = "fake"
    BHARATCODE = "bharatcode"


class ContextChoice(StrEnum):
    METADATA = "metadata"
    SNIPPETS = "snippets"


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
    console: Console,
    code: int,
    fail_on: FailOn,
    target: str,
    *,
    command: str = "scan",
    report_path: str | None = None,
) -> None:
    """The exit line with next-step hints. Hints name only commands that exist (ADR-025
    item 5): ``test`` after a scan, ``explain <report>`` when ``report_path`` is given (the
    caller passes it only when the report has something to explain; ADR-032)."""
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
    if report_path:
        hints.append(f"flightcheck explain {shlex.quote(report_path)}")
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


REDACTION_SENTENCE = "Secrets matching known patterns have been redacted; review with --dry-run"


class ConsentView(Protocol):
    """The fields of ``diagnostics.ConsentInfo`` the panel shows (cli may not import
    diagnostics; the orchestrator hands the object over)."""

    destination: str
    model: str
    context: str
    groups: int
    findings: int
    characters: int
    calls: int
    files: list[str]
    notes: list[str]


def is_interactive(environ: dict[str, str] | None = None) -> bool:
    """A TTY on stdin and not CI (``CI=true``, §19.3)."""
    env = os.environ if environ is None else environ
    if env.get("CI", "").strip().lower() in ("1", "true", "yes"):
        return False
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def render_destination(console: Console, *, backend: str, destination: str | None) -> None:
    """§20.5: the destination host is printed on every explain run."""
    if backend == "bharatcode" and destination:
        line = f"Destination: {destination} (BharatCode; sent only after consent)"
    else:
        line = f"Destination: none — backend '{backend}' runs locally; nothing is sent"
    console.print(Text(redact(line)), soft_wrap=True)


def render_consent_panel(console: Console, info: ConsentView) -> None:
    """The consent screen (§19.3). Everything in it is redacted."""
    files = ", ".join(info.files) if info.files else "none (no source code is sent)"
    lines = [
        f"Destination: {info.destination}",
        f"Model:       {info.model}",
        f"Context:     {info.context}",
        f"Findings:    {info.findings} in {info.groups} group(s), {info.calls} request(s)",
        f"Characters:  {info.characters}",
        f"Files:       {files}",
        *[f"Note:        {note}" for note in info.notes],
        REDACTION_SENTENCE + ".",
    ]
    console.print(
        Panel(
            Text(redact("\n".join(lines))),
            title="Send to BharatCode?",
            title_align="left",
            border_style="yellow",
            expand=False,
        )
    )


def render_dry_run(console: Console, payloads: list[dict[str, Any]], destination: str) -> None:
    """``--dry-run``: the exact request bodies, redacted, and a "nothing sent" line."""
    console.print(
        Text(f"Dry run: the {len(payloads)} request(s) that --llm bharatcode would send."),
        soft_wrap=True,
    )
    for number, payload in enumerate(payloads, 1):
        console.print(
            Text(f"Request {number} of {len(payloads)}: POST {redact(destination)}"),
            soft_wrap=True,
        )
        console.print(
            Text(redact(json.dumps(payload, indent=2, ensure_ascii=False))), soft_wrap=True
        )
    console.print(Text("Dry run: nothing was sent and no report was written."), soft_wrap=True)


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
    "REDACTION_SENTENCE",
    "ConsentView",
    "ContextChoice",
    "FailOn",
    "LLMChoice",
    "OutputFormat",
    "ProviderChoice",
    "ReportFormat",
    "exceeds_fail_on",
    "is_interactive",
    "make_console",
    "render_consent_panel",
    "render_destination",
    "render_dry_run",
    "render_error",
    "render_exec_warning",
    "render_exit_line",
    "render_internal_error",
]
