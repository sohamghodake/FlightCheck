"""Typer application. Parses arguments, calls the orchestrator, renders, maps exit codes.

Exit codes (plan §23.1): 0 = no finding at or above ``--fail-on``; 1 = at least one;
2 = usage, configuration or consent error; 3 = internal error.
"""

from pathlib import Path
from typing import Annotated

import typer

from flightcheck import __version__
from flightcheck.cli.render import (
    FailOn,
    OutputFormat,
    ProviderChoice,
    exceeds_fail_on,
    make_console,
    render_error,
    render_exit_line,
    render_internal_error,
)
from flightcheck.core.config import load_config
from flightcheck.core.errors import ConfigError, FlightCheckError
from flightcheck.core.logging import configure_logging
from flightcheck.core.models import ScanRequest, Severity
from flightcheck.core.paths import default_output_dir
from flightcheck.orchestrator import run_scan
from flightcheck.reporting.json_report import write_report
from flightcheck.reporting.terminal import render_scan

HELP = """\
FlightCheck checks Python AI applications for BharatCode API integration mistakes.

\b
Available:
  scan PATH                 static analysis against BharatCode's documented contract

\b
Coming in later phases (not available yet):
  test PATH --allow-exec    run the app against a local traffic-recording mock
  explain REPORT.json       opt-in explanations of findings using BharatCode
  report REPORT.json        render a saved report (HTML or Markdown)
"""

app = typer.Typer(
    name="flightcheck",
    help=HELP,
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode=None,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"flightcheck {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def root(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            help="Show the FlightCheck version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = False,
) -> None:
    """FlightCheck checks Python AI applications for BharatCode API integration mistakes."""


def _display(path: Path) -> str:
    """Path relative to the working directory when possible (no absolute paths needed)."""
    try:
        return str(path.relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)


def _scan(
    path: Path,
    fmt: OutputFormat,
    output: Path | None,
    provider: ProviderChoice,
    rules: list[str] | None,
    min_severity: Severity,
    fail_on: FailOn,
    fail_on_hypotheses: bool,
    no_color: bool,
) -> int:
    if fmt in (OutputFormat.HTML, OutputFormat.ALL):
        raise ConfigError(
            "HTML report arrives in Phase 9.", hint="Use --format terminal or --format json."
        )
    console = make_console(no_color=no_color)
    request = ScanRequest(
        root=path,
        provider_override=None if provider is ProviderChoice.AUTO else provider.value,
        rule_ids=rules or None,
        min_severity=min_severity,
    )
    report = run_scan(request, load_config(path))
    out_dir = output if output is not None else default_output_dir(path)
    shown_path = _display(write_report(report, out_dir))
    code = 1 if exceeds_fail_on(report, fail_on, include_hypotheses=fail_on_hypotheses) else 0
    if fmt is OutputFormat.JSON:
        console.print(f"Report written: {shown_path}", markup=False, soft_wrap=True)
        return code
    if report.repo is not None and report.repo.python_files_scanned == 0:
        console.print(
            f"No Python files found under {path}; nothing to scan.", markup=False, highlight=False
        )
    render_scan(console, report, target=str(path), report_path=shown_path)
    render_exit_line(console, code, fail_on, str(path))
    return code


@app.command()
def scan(
    path: Annotated[
        Path, typer.Argument(help="Directory (or single .py file) to scan.", show_default=True)
    ] = Path("."),
    fmt: Annotated[
        OutputFormat,
        typer.Option("--format", help="Output format; the JSON report is always written."),
    ] = OutputFormat.TERMINAL,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output", help="Directory for the JSON report [default: PATH/.flightcheck/]."
        ),
    ] = None,
    provider: Annotated[
        ProviderChoice,
        typer.Option("--provider", help="Override provider detection."),
    ] = ProviderChoice.AUTO,
    rule: Annotated[
        list[str] | None,
        typer.Option("--rule", help="Run only this rule ID (repeatable)."),
    ] = None,
    min_severity: Annotated[
        Severity,
        typer.Option("--min-severity", help="Hide findings below this severity."),
    ] = Severity.INFO,
    fail_on: Annotated[
        FailOn,
        typer.Option("--fail-on", help="Exit 1 when a finding is at or above this severity."),
    ] = FailOn.HIGH,
    fail_on_hypotheses: Annotated[
        bool,
        typer.Option("--fail-on-hypotheses", help="Let hypothesis findings trigger --fail-on."),
    ] = False,
    no_color: Annotated[bool, typer.Option("--no-color", help="Disable colour output.")] = False,
    verbose: Annotated[
        int, typer.Option("--verbose", "-v", count=True, help="More logging (-v, -vv).")
    ] = 0,
) -> None:
    """Static analysis against BharatCode's documented contract (no code is executed)."""
    configure_logging(verbose)
    err = make_console(no_color=no_color, stderr=True)
    try:
        code = _scan(
            path, fmt, output, provider, rule, min_severity, fail_on, fail_on_hypotheses, no_color
        )
    except FlightCheckError as exc:
        render_error(err, exc)
        code = exc.exit_code
    except Exception as exc:  # unexpected: exit 3 with a hint
        render_internal_error(err, exc, verbose=verbose > 0)
        code = 3
    raise typer.Exit(code)


def main() -> None:
    """Console-script entry point."""
    app()
