"""Terminal rendering of a scan report with Rich (plan §23.3, §24).

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
from flightcheck.core.models import Finding, FindingKind, ProviderDetection, Report, Severity
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
    checks = sum(len(s.checks) for s in report.scenarios)
    parts = [head, f"{checks} runtime checks"]
    if report_path:
        parts.append(f"report: {report_path}")
    console.print()
    console.print(Text(redact(" · ".join(parts))), soft_wrap=True)


def render_scan(console: Console, report: Report, *, target: str, report_path: str | None) -> None:
    """Header, findings grouped by file, diagnostics and the summary line."""
    render_header(console, report, target)
    render_findings(console, report.findings)
    render_diagnostics(console, report)
    render_summary(console, report, report_path)


__all__ = ["render_scan", "severity_counts"]
