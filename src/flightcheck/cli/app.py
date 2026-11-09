"""Typer application. Parses arguments, calls the orchestrator, renders, maps exit codes.

Exit codes (plan §23.1): 0 = no finding at or above ``--fail-on``; 1 = at least one (failed
runtime checks count through their mirrored runtime findings; needs_review never does);
2 = usage, configuration or consent error; 3 = internal error (including the mock failing to
start). ``explain`` exits 0 on success *or* fallback, 2 for usage/consent/report errors.
"""

from pathlib import Path
from typing import Annotated

import typer

from flightcheck import __version__
from flightcheck.cli.render import (
    ContextChoice,
    FailOn,
    LLMChoice,
    OutputFormat,
    ProviderChoice,
    exceeds_fail_on,
    is_interactive,
    make_console,
    render_consent_panel,
    render_destination,
    render_dry_run,
    render_error,
    render_exec_warning,
    render_exit_line,
    render_internal_error,
)
from flightcheck.core.config import load_config
from flightcheck.core.errors import ConfigError, ConsentRequiredError, FlightCheckError
from flightcheck.core.logging import configure_logging
from flightcheck.core.models import ScanRequest, Severity, TestRequest
from flightcheck.core.paths import default_output_dir, resolve_inside
from flightcheck.orchestrator import (
    ConsentInfo,
    ExplainOptions,
    plan_explain,
    plan_test,
    require_consent,
    run_explain,
    run_scan,
    run_test,
)
from flightcheck.reporting.json_report import (
    read_report,
    report_to_json,
    write_report,
    write_traffic,
)
from flightcheck.reporting.terminal import render_explain, render_scan, render_test

HELP = """\
FlightCheck checks Python AI applications for BharatCode API integration mistakes.

\b
Available:
  scan PATH                 static analysis against BharatCode's documented contract
  test PATH --allow-exec    run the app against a local traffic-recording mock
  explain REPORT.json       opt-in explanations of findings using BharatCode

\b
Coming in later phases (not available yet):
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


def _test(
    path: Path,
    allow_exec: bool,
    scenarios: list[str] | None,
    config_file: Path | None,
    timeout: int | None,
    fmt: OutputFormat,
    output: Path | None,
    fail_on: FailOn,
    no_scan: bool,
    keep_traffic: bool,
    no_color: bool,
) -> int:
    if fmt in (OutputFormat.HTML, OutputFormat.ALL):
        raise ConfigError(
            "HTML report arrives in Phase 9.", hint="Use --format terminal or --format json."
        )
    if config_file is not None:
        raise ConfigError(
            "--config is not supported yet (P1, ADR-030).",
            hint=f"Put flightcheck.toml in {path} and run flightcheck test {path} --allow-exec.",
        )
    # Consent before anything else: --allow-exec is never implied by configuration (§27 7B).
    require_consent(allow_exec, target=str(path))
    request = TestRequest(
        root=path, allow_exec=allow_exec, scenarios=scenarios or None, include_scan=not no_scan
    )
    config = load_config(path)
    if timeout is not None and config.test is not None:  # no [test]: plan_test explains
        config = load_config(path, {"test": {"timeout_s": timeout}})
    plan = plan_test(request, config)  # validates [test], scenarios, cwd; pre-flight; no run
    render_exec_warning(
        make_console(no_color=no_color, stderr=True),
        command=plan.test.command,
        cwd=_display(plan.cwd.resolve()),
        scenarios=[(s.id, s.name) for s in plan.scenarios],
        timeout_s=plan.test.timeout_s,
        refusal=plan.preflight_refusal,
    )
    report = run_test(request, config, plan=plan)
    out_dir = output if output is not None else default_output_dir(path)
    shown_path = _display(write_report(report, out_dir))
    traffic_path = _display(write_traffic(report, out_dir)) if keep_traffic else None
    code = 1 if exceeds_fail_on(report, fail_on, include_hypotheses=False) else 0
    console = make_console(no_color=no_color)
    if fmt is OutputFormat.JSON:
        console.print(f"Report written: {shown_path}", markup=False, soft_wrap=True)
        if traffic_path:
            console.print(f"Traffic written: {traffic_path}", markup=False, soft_wrap=True)
        return code
    render_test(console, report, target=str(path), report_path=shown_path)
    if traffic_path:
        console.print(f"Traffic written: {traffic_path}", markup=False, soft_wrap=True)
    render_exit_line(console, code, fail_on, str(path), command="test")
    return code


@app.command()
def test(
    path: Annotated[
        Path, typer.Argument(help="Directory with flightcheck.toml and your app.")
    ] = Path("."),
    allow_exec: Annotated[
        bool,
        typer.Option("--allow-exec", help="Required: allow FlightCheck to run the [test] command."),
    ] = False,
    scenario: Annotated[
        list[str] | None,
        typer.Option("--scenario", help="Run only this scenario ID (repeatable)."),
    ] = None,
    config_file: Annotated[
        Path | None, typer.Option("--config", help="Not supported yet (P1).")
    ] = None,
    timeout: Annotated[
        int | None,
        typer.Option("--timeout", min=1, max=600, help="Seconds per scenario [default: config]."),
    ] = None,
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
    fail_on: Annotated[
        FailOn,
        typer.Option(
            "--fail-on", help="Exit 1 when a finding or failed check is at or above this."
        ),
    ] = FailOn.HIGH,
    no_scan: Annotated[
        bool, typer.Option("--no-scan", help="Do not merge static scan findings.")
    ] = False,
    keep_traffic: Annotated[
        bool,
        typer.Option(
            "--keep-traffic", help="Also write flightcheck-traffic.json (redacted exchanges)."
        ),
    ] = False,
    no_color: Annotated[bool, typer.Option("--no-color", help="Disable colour output.")] = False,
    verbose: Annotated[
        int, typer.Option("--verbose", "-v", count=True, help="More logging (-v, -vv).")
    ] = 0,
) -> None:
    """Run the app against a local traffic-recording mock (your code runs; --allow-exec)."""
    configure_logging(verbose)
    err = make_console(no_color=no_color, stderr=True)
    try:
        code = _test(
            path,
            allow_exec,
            scenario,
            config_file,
            timeout,
            fmt,
            output,
            fail_on,
            no_scan,
            keep_traffic,
            no_color,
        )
    except FlightCheckError as exc:
        render_error(err, exc)
        if exc.exit_code == 3 and not exc.hint:
            err.print("Re-run with -v for details.", markup=False, style="dim")
        code = exc.exit_code
    except Exception as exc:  # unexpected: exit 3 with a hint
        render_internal_error(err, exc, verbose=verbose > 0)
        code = 3
    raise typer.Exit(code)


def _explained_path(report_path: Path, output: Path | None) -> Path:
    target = (
        output
        if output is not None
        else report_path.with_name(f"{report_path.stem}.explained.json")
    )
    if target.exists() and target.is_dir():
        raise ConfigError(f"--output is a directory, not a file: {target}")
    if target.resolve() == report_path.resolve():
        raise ConfigError(
            "--output would overwrite the input report.", hint="Choose another --output FILE."
        )
    return target


def _explain(
    report_path: Path,
    llm: LLMChoice | None,
    context: ContextChoice | None,
    repo: Path,
    max_findings: int | None,
    model: str | None,
    yes: bool,
    dry_run: bool,
    output: Path | None,
    fmt: OutputFormat,
    no_color: bool,
) -> int:
    if fmt in (OutputFormat.HTML, OutputFormat.ALL):
        raise ConfigError(
            "HTML report arrives in Phase 9.", hint="Use --format terminal or --format json."
        )
    err = make_console(no_color=no_color, stderr=True)
    console = make_console(no_color=no_color)
    report = read_report(report_path)  # unknown schema major / not a report → exit 2
    target = _explained_path(report_path, output)
    overrides = {
        "explain": {
            "llm": llm.value if llm else None,
            "context": context.value if context else None,
            "model": model,
            "max_groups": max_findings,
        }
    }
    config = load_config(repo, overrides)

    def consent(info: ConsentInfo) -> bool:
        # Only asked when data would leave the process (backend bharatcode, ADR-031).
        render_consent_panel(err, info)
        if yes:
            return True
        if not is_interactive():
            raise ConsentRequiredError(
                f"explain would send {info.findings} finding(s) to {info.destination}, and "
                "this session is not interactive (no TTY, or CI=true).",
                hint="Review the payload with --dry-run, then re-run with --yes.",
            )
        return typer.confirm("Proceed?", default=False, err=True)

    options = ExplainOptions(repo=repo, dry_run=dry_run, consent=consent)
    plan = plan_explain(report, config, options)
    if plan.nothing_to_explain:
        console.print(
            "Nothing to explain: the report has no findings and no checks that need review.",
            markup=False,
        )
        return 0
    render_destination(err, backend=plan.backend, destination=plan.destination)
    for note in plan.notes:
        err.print(f"Note: {note}", markup=False, soft_wrap=True)
    if dry_run:
        render_dry_run(console, plan.payloads(), plan.destination)
        return 0
    explained = run_explain(report, config, options, plan=plan)
    target.parent.mkdir(parents=True, exist_ok=True)
    written = resolve_inside(target.parent, target.name)
    written.write_text(report_to_json(explained), encoding="utf-8")
    shown_path = _display(written)
    if fmt is OutputFormat.JSON:
        console.print(f"Explained report written: {shown_path}", markup=False, soft_wrap=True)
        return 0
    render_explain(console, explained, report_path=shown_path)
    return 0


@app.command()
def explain(
    report: Annotated[Path, typer.Argument(help="A FlightCheck JSON report (scan or test).")],
    llm: Annotated[
        LLMChoice | None,
        typer.Option(
            "--llm", help="Backend [default: bharatcode if BHARATCODE_API_KEY is set, else none]."
        ),
    ] = None,
    context: Annotated[
        ContextChoice | None,
        typer.Option("--context", help="What is sent [default: metadata; no source code]."),
    ] = None,
    repo: Annotated[
        Path, typer.Option("--repo", help="Project directory (snippets, flightcheck.toml).")
    ] = Path("."),
    max_findings: Annotated[
        int | None,
        typer.Option("--max-findings", min=1, help="Finding groups to explain [default: 5]."),
    ] = None,
    model: Annotated[
        str | None, typer.Option("--model", help="Model ID [default: provider default].")
    ] = None,
    yes: Annotated[
        bool, typer.Option("--yes", help="Consent to sending the shown data to BharatCode.")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print the exact payload; send nothing.")
    ] = False,
    output: Annotated[
        Path | None,
        typer.Option("--output", help="Output file [default: <report>.explained.json]."),
    ] = None,
    fmt: Annotated[
        OutputFormat,
        typer.Option("--format", help="Output format; the explained JSON is always written."),
    ] = OutputFormat.TERMINAL,
    no_color: Annotated[bool, typer.Option("--no-color", help="Disable colour output.")] = False,
    verbose: Annotated[
        int, typer.Option("--verbose", "-v", count=True, help="More logging (-v, -vv).")
    ] = 0,
) -> None:
    """Explain findings with BharatCode (opt-in, consented) or the deterministic fallback."""
    configure_logging(verbose)
    err = make_console(no_color=no_color, stderr=True)
    try:
        code = _explain(
            report,
            llm,
            context,
            repo,
            max_findings,
            model,
            yes,
            dry_run,
            output,
            fmt,
            no_color,
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
