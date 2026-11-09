"""Terminal rendering of scan and test reports with Rich (plan §23.2, §23.3, §24).

All text is passed as ``rich.text.Text`` (never as markup), so ``[UNVERIFIED]`` and source
excerpts are printed literally. Every string from the report is redacted again here.
"""

from __future__ import annotations

from collections import Counter
from itertools import groupby

from rich.console import Console
from rich.table import Table
from rich.text import Text

from flightcheck import __version__
from flightcheck.core.models import (
    AISuggestion,
    CheckResult,
    CheckStatus,
    Evidence,
    Finding,
    FindingKind,
    ProviderDetection,
    Report,
    ScenarioResult,
    Severity,
)
from flightcheck.core.redact import redact

SEVERITY_STYLES = {
    Severity.CRITICAL: "bold white on red",
    Severity.HIGH: "bold red",
    Severity.MEDIUM: "yellow",
    Severity.LOW: "cyan",
    Severity.INFO: "dim",
}
REPO_LOCATION_PATH = "."


def _repo_detection(report: Report) -> ProviderDetection | None:
    return next(
        (d for d in reversed(report.providers_detected) if d.location.path == REPO_LOCATION_PATH),
        None,
    )


def _badge(finding: Finding) -> str:
    level = "hypothesis" if finding.kind is FindingKind.HYPOTHESIS else finding.verification.value
    return f"{level} · {finding.confidence.value}"


def severity_counts(findings: list[Finding]) -> str:
    counts = Counter(f.severity for f in findings)
    return ", ".join(f"{counts[s]} {s.value}" for s in Severity if counts[s])


def render_header(console: Console, report: Report, target: str) -> None:
    console.print(Text(f"FlightCheck {__version__} · {report.metadata.command} · {target}"))
    files = report.repo.python_files_scanned if report.repo else 0
    repo = _repo_detection(report)
    if repo is None:
        providers = "none detected"
    else:
        reason = f": {repo.signals[0]}" if repo.signals else ""
        providers = f"{repo.provider} ({repo.confidence.value}{reason})"
    noun = "file" if files == 1 else "files"
    console.print(Text(redact(f"Providers: {providers} · {files} {noun} scanned")))


def render_findings(console: Console, findings: list[Finding]) -> None:
    for path, group in groupby(findings, key=lambda f: f.location.path if f.location else ""):
        console.print()
        console.print(Text(f" {path or '(repository)'}", style="bold"))
        table = Table(box=None, show_header=False, padding=(0, 1), pad_edge=True, expand=False)
        table.add_column("severity", no_wrap=True, min_width=6)
        table.add_column("rule", no_wrap=True, min_width=10)
        table.add_column("line", no_wrap=True, min_width=5)
        table.add_column("message", overflow="fold", ratio=1)
        table.add_column("verification", no_wrap=True)
        for finding in group:
            line = f"L{finding.location.line}" if finding.location else ""
            table.add_row(
                Text(finding.severity.value.upper(), style=SEVERITY_STYLES[finding.severity]),
                Text(finding.rule_id),
                Text(line),
                Text(redact(finding.message)),
                Text(_badge(finding), style="dim"),
            )
        console.print(table)


def render_diagnostics(console: Console, report: Report) -> None:
    shown = [d for d in report.diagnostics if d.level in ("warning", "error")]
    infos = len(report.diagnostics) - len(shown)
    if not shown and not infos:
        return
    console.print()
    for diag in shown:
        where = f" {diag.path}:" if diag.path else ""
        style = "red" if diag.level == "error" else "yellow"
        console.print(
            Text(f"{diag.level.upper()} {diag.code}", style=style)
            + Text(redact(f"{where} {diag.message}"))
        )
    if infos:
        noun = "diagnostic" if infos == 1 else "diagnostics"
        console.print(Text(f"{infos} info {noun} in the JSON report.", style="dim"))


def render_summary(console: Console, report: Report, report_path: str | None) -> None:
    count = len(report.findings)
    if count:
        noun = "finding" if count == 1 else "findings"
        head = f"{count} {noun} ({severity_counts(report.findings)})"
    else:
        head = "No findings"
    checks = [c for s in report.scenarios for c in s.checks]
    counts = Counter(c.status for c in checks)
    detail = ", ".join(
        f"{counts[status]} {_STATUS_LABELS[status]}"
        for status in (CheckStatus.FAILED, CheckStatus.NEEDS_REVIEW)
        if counts[status]
    )
    parts = [head, f"{len(checks)} runtime checks" + (f" ({detail})" if detail else "")]
    if report_path:
        parts.append(f"report: {report_path}")
    console.print()
    console.print(Text(redact(" · ".join(parts))), soft_wrap=True)


_STATUS_LABELS = {
    CheckStatus.PASSED: "passed",
    CheckStatus.FAILED: "failed",
    CheckStatus.NEEDS_REVIEW: "needs review",
    CheckStatus.SKIPPED: "skipped",
    CheckStatus.UNSUPPORTED: "unsupported",
    CheckStatus.ERROR: "error",
}
STATUS_STYLES = {
    CheckStatus.PASSED: "green",
    CheckStatus.FAILED: "bold red",
    CheckStatus.NEEDS_REVIEW: "yellow",
    CheckStatus.SKIPPED: "dim",
    CheckStatus.UNSUPPORTED: "dim",
    CheckStatus.ERROR: "red",
}
_COUNT_COLUMNS = (
    CheckStatus.PASSED,
    CheckStatus.FAILED,
    CheckStatus.NEEDS_REVIEW,
    CheckStatus.SKIPPED,
    CheckStatus.UNSUPPORTED,
)


def _app_exit(result: ScenarioResult) -> str:
    run = result.app_run
    if run is None:
        return "not run"
    if run.timed_out:
        return "timeout"
    return f"exit {run.exit_code}"


def render_scenario_table(console: Console, report: Report) -> None:
    """One row per scenario: app exit, requests seen and check counts per status (§23.2)."""
    show_error = any(c.status is CheckStatus.ERROR for s in report.scenarios for c in s.checks)
    columns = (*_COUNT_COLUMNS, CheckStatus.ERROR) if show_error else _COUNT_COLUMNS
    table = Table(box=None, padding=(0, 1), pad_edge=True, expand=False, header_style="bold")
    table.add_column("scenario", no_wrap=True)
    table.add_column("name", overflow="fold")
    table.add_column("status", no_wrap=True)
    table.add_column("app", no_wrap=True)
    table.add_column("requests", no_wrap=True, justify="right")
    for status in columns:
        table.add_column(_STATUS_LABELS[status], no_wrap=True, justify="right")
    for result in report.scenarios:
        counts = Counter(c.status for c in result.checks)
        table.add_row(
            Text(result.scenario_id),
            Text(result.name),
            Text(_STATUS_LABELS[result.status], style=STATUS_STYLES[result.status]),
            Text(_app_exit(result)),
            Text(str(len(result.exchanges))),
            *[
                Text(str(counts[s]), style=STATUS_STYLES[s] if counts[s] else "dim")
                for s in columns
            ],
        )
    console.print()
    console.print(Text(" Scenarios", style="bold"))
    console.print(table)


def _one_line(text: str) -> str:
    return " ⏎ ".join(part.strip() for part in text.strip().splitlines() if part.strip())


def _evidence_line(item: Evidence) -> Text:
    label = item.type if item.exchange_seq is None else f"{item.type} #{item.exchange_seq}"
    body = _one_line(item.excerpt)
    if item.note:
        body = f"{body} ({item.note})" if body else item.note
    return Text("      ") + Text(f"{label}: ", style="dim") + Text(redact(body))


def _render_check(console: Console, check: CheckResult) -> None:
    head = Text(" ") + Text(
        f"{_STATUS_LABELS[check.status].upper():<12}", style=STATUS_STYLES[check.status]
    )
    head += Text(f" {check.severity.value.upper():<8}", style=SEVERITY_STYLES[check.severity])
    head += Text(f" {check.check_id} [{check.scenario_id}] ") + Text(redact(check.message))
    console.print(head)
    if check.expected or check.observed:
        console.print(
            Text("      ")
            + Text(redact(f"expected: {check.expected or '—'} · observed: {check.observed or '—'}"))
        )
    for item in check.evidence:
        console.print(_evidence_line(item))


def render_check_details(console: Console, report: Report) -> None:
    """Errors (FlightCheck could not run the check), failed checks, then needs_review, each
    with its traffic/process excerpts. An error repeated across a scenario's checks (the app
    could not start) is shown once per scenario."""
    errors: dict[tuple[str, str], CheckResult] = {}
    for result in report.scenarios:
        for check in result.checks:
            if check.status is CheckStatus.ERROR:
                errors.setdefault((check.scenario_id, check.message), check)
    if errors:
        console.print()
        console.print(Text(" Errors", style="bold"))
        for check in errors.values():
            console.print(
                Text(" ")
                + Text("ERROR".ljust(12), style=STATUS_STYLES[CheckStatus.ERROR])
                + Text(f" [{check.scenario_id}] ")
                + Text(redact(check.message))
            )
    for status in (CheckStatus.FAILED, CheckStatus.NEEDS_REVIEW):
        checks = [c for s in report.scenarios for c in s.checks if c.status is status]
        if not checks:
            continue
        console.print()
        console.print(Text(f" {_STATUS_LABELS[status].capitalize()} checks", style="bold"))
        for check in checks:
            _render_check(console, check)


def render_test(console: Console, report: Report, *, target: str, report_path: str | None) -> None:
    """Header, static findings (when the scan ran), the scenario table, failed and
    needs_review details with excerpts, diagnostics and the summary line."""
    render_header(console, report, target)
    render_findings(console, [f for f in report.findings if f.kind is not FindingKind.RUNTIME])
    render_scenario_table(console, report)
    render_check_details(console, report)
    render_diagnostics(console, report)
    render_summary(console, report, report_path)


def render_scan(console: Console, report: Report, *, target: str, report_path: str | None) -> None:
    """Header, findings grouped by file, diagnostics and the summary line."""
    render_header(console, report, target)
    render_findings(console, report.findings)
    render_diagnostics(console, report)
    render_summary(console, report, report_path)


# --- explain (Phase 8) ----------------------------------------------------------------------

# What the user can do about a fallback (reason names: ADR-031). Unknown reasons get none.
FALLBACK_HINTS = {
    "no_key": "No BHARATCODE_API_KEY is set: showing the rule's remediation. Export the key "
    "and re-run with --llm bharatcode, or use --llm fake for an offline demo.",
    "llm_none": "--llm none: showing the rule's remediation.",
    "network_disabled": "FLIGHTCHECK_NO_NETWORK is set, so nothing was sent.",
    "consent_declined": "You declined; nothing was sent.",
    "provider_busy": "BharatCode stayed busy (503) after retries. Re-run later.",
    "auth_or_plan": "BharatCode refused the key (401/403). Check that the key is valid and "
    "that your plan (student, Lite or Pro) includes the model.",
    "truncated": "The answer was cut off even with a doubled output budget.",
    "invalid_output": "The answer was not a valid explanation batch.",
    "rejected": "The explanation failed FlightCheck's validation and was dropped.",
    "missing_explanation": "The answer had no explanation for this group.",
    "call_limit": "Over the per-run call limit (max_calls); not sent.",
    "input_too_large": "This group alone exceeds the per-call input cap; not sent.",
    "timeout": "The request timed out.",
    "connection_error": "Could not connect to BharatCode.",
    "provider_error": "BharatCode returned an error.",
    "internal_error": "FlightCheck hit an unexpected error for this call.",
}


def ai_label(suggestion: AISuggestion) -> str:
    if suggestion.backend == "fake":
        return "AI suggestion (fake LLM, offline; not generated by BharatCode) — not verified"
    return f"AI suggestion (BharatCode {suggestion.model or 'unknown model'}) — not verified"


def _render_suggestion(console: Console, suggestion: AISuggestion) -> None:
    count = len(suggestion.finding_ids)
    console.print()
    console.print(
        Text(" ")
        + Text(
            suggestion.severity.value.upper().ljust(8), style=SEVERITY_STYLES[suggestion.severity]
        )
        + Text(f" {suggestion.rule_id}", style="bold")
        + Text(f"  {redact(suggestion.title)}  ({count} instance{'s' if count != 1 else ''})"),
        soft_wrap=True,
    )
    explanation = suggestion.explanation
    if explanation is None:
        reason = suggestion.fallback_reason or "unknown"
        hint = FALLBACK_HINTS.get(reason, "")
        detail = f" ({redact(suggestion.rejected_reason)})" if suggestion.rejected_reason else ""
        remediation = suggestion.remediation or "No deterministic remediation is known."
        grid = Table.grid(padding=(0, 1))
        grid.add_column(no_wrap=True)
        grid.add_column(overflow="fold")
        grid.add_row(
            Text("   Fallback:", style="dim"),
            Text(f"{reason}{detail}. {hint}".rstrip(), style="dim"),
        )
        grid.add_row(Text("   Remediation:"), Text(redact(remediation) + " (deterministic)"))
        console.print(grid)
        return
    console.print(Text(f"   {ai_label(suggestion)}", style="magenta"))
    grid = Table.grid(padding=(0, 1))
    grid.add_column(no_wrap=True)
    grid.add_column(overflow="fold")
    grid.add_row(Text("   Summary:"), Text(redact(explanation.summary)))
    grid.add_row(Text("   Root cause:"), Text(redact(explanation.root_cause)))
    grid.add_row(Text("   Impact:"), Text(redact(explanation.impact)))
    for number, step in enumerate(explanation.fix_steps, 1):
        grid.add_row(Text(f"   Fix {number}:"), Text(redact(step)))
    for caveat in explanation.caveats:
        grid.add_row(Text("   Caveat:", style="dim"), Text(redact(caveat), style="dim"))
    grid.add_row(
        Text("   Confidence:", style="dim"),
        Text(f"{explanation.model_stated_confidence} (model-stated)", style="dim"),
    )
    console.print(grid)
    if suggestion.diff:
        console.print(Text(redact(suggestion.diff), style="cyan"))


def render_explain(console: Console, report: Report, *, report_path: str | None) -> None:
    """Per group: the AI explanation (labelled, never verified) or the fallback notice with
    the deterministic remediation; then the explained report's path."""
    suggestions = report.ai_suggestions
    console.print(
        Text(f"FlightCheck {__version__} · explain · {len(suggestions)} finding group(s)"),
        soft_wrap=True,
    )
    for suggestion in suggestions:
        _render_suggestion(console, suggestion)
    explained = report.summary.ai_suggestions
    fallbacks = len(suggestions) - explained
    console.print()
    console.print(
        Text(
            f"{explained} explained, {fallbacks} fallback(s). AI output never changes findings, "
            "checks or exit codes."
        ),
        soft_wrap=True,
    )
    if report_path:
        console.print(Text(f"Explained report: {report_path}"), soft_wrap=True)


__all__ = ["render_explain", "render_scan", "render_test", "severity_counts"]
