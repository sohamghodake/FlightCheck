"""Context selection for ``explain`` (plan §19.3, ADR-020, ADR-031).

1. Select every finding (static, runtime, hypothesis), every ``needs_review`` check, and any
   ``failed`` check that has no mirrored runtime finding (``Finding.check_ref``).
2. Group by rule ID (a check's ``check_id``), order by severity, then instance count, then rule
   ID; keep ``max_groups`` groups and at most 3 instances per group.
3. ``metadata`` (default): IDs, title, remediation, message, relative path and lines, provider,
   and a check's expected/observed (capped). No source code and no traffic bodies.
   ``snippets``: also ±8 lines around each finding (≤ 40 lines per instance, ≤ 12 000
   characters in total, files read only through ``resolve_inside`` under ``--repo``) and the
   redacted request body of a check's traffic evidence (≤ 2 000 characters per check).

A ``needs_review`` check carries no title or remediation, and diagnostics may not import the
runtime's check catalogue: the orchestrator injects ``{check_id: (title, remediation)}``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from flightcheck.core.errors import PathOutsideRootError
from flightcheck.core.models import (
    CheckResult,
    CheckStatus,
    Finding,
    RecordedExchange,
    Report,
    Severity,
)
from flightcheck.core.paths import resolve_inside
from flightcheck.diagnostics.sanitize import Sanitizer

ContextLevel = Literal["metadata", "snippets"]

INSTANCES_PER_GROUP = 3
SNIPPET_RADIUS = 8
SNIPPET_MAX_LINES = 40
SNIPPET_TOTAL_CHARS = 12_000
TRAFFIC_CHARS = 2_000
METADATA_FIELD_CHARS = 300  # expected/observed at the metadata level
MAX_SOURCE_BYTES = 2 * 1024 * 1024

CheckCatalogue = Mapping[str, tuple[str, str]]  # check_id -> (title, remediation)

__all__ = [
    "INSTANCES_PER_GROUP",
    "CheckCatalogue",
    "ContextLevel",
    "Group",
    "Instance",
    "Selection",
    "select",
]


@dataclass
class Instance:
    finding_id: str  # Finding.id or CheckResult.id
    message: str
    source: Literal["finding", "check"]
    kind: str  # finding kind, or "check"
    verification: str
    status: str | None = None  # check status
    scenario_id: str | None = None
    path: str | None = None
    line: int | None = None
    end_line: int | None = None
    provider: str | None = None
    expected: str | None = None
    observed: str | None = None
    snippet: str | None = None
    snippet_start_line: int | None = None
    traffic_excerpt: str | None = None

    def to_prompt(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "finding_id": self.finding_id,
            "kind": self.kind,
            "verification": self.verification,
            "message": self.message,
        }
        optional = {
            "status": self.status,
            "scenario_id": self.scenario_id,
            "path": self.path,
            "line": self.line,
            "end_line": self.end_line,
            "provider": self.provider,
            "expected": self.expected,
            "observed": self.observed,
            "snippet_start_line": self.snippet_start_line,
            "snippet": self.snippet,
            "request_body_excerpt": self.traffic_excerpt,
        }
        data.update({key: value for key, value in optional.items() if value is not None})
        return data


@dataclass
class Group:
    rule_id: str
    title: str
    severity: Severity
    remediation: str
    total: int  # instances in the report (only the first 3 are sent)
    instances: list[Instance] = field(default_factory=list)

    @property
    def instance_ids(self) -> list[str]:
        return [instance.finding_id for instance in self.instances]

    def snippets_by_path(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for instance in self.instances:
            if instance.path and instance.snippet:
                out.setdefault(instance.path, []).append(instance.snippet)
        return out

    def to_prompt(self) -> dict[str, Any]:
        verifications = sorted({instance.verification for instance in self.instances})
        return {
            "rule_id": self.rule_id,
            "title": self.title,
            "severity": self.severity.value,
            "verification": ", ".join(verifications),
            "remediation": self.remediation,
            "instances_in_report": self.total,
            "instances": [instance.to_prompt() for instance in self.instances],
        }


@dataclass
class Selection:
    level: ContextLevel
    groups: list[Group]
    dropped_groups: int  # groups beyond max_groups
    notes: list[str] = field(default_factory=list)  # e.g. "snippet omitted: … not found"

    @property
    def instance_count(self) -> int:
        return sum(len(group.instances) for group in self.groups)

    @property
    def files(self) -> list[str]:
        """Repo-relative files whose lines are included (snippets level only)."""
        return sorted({i.path for g in self.groups for i in g.instances if i.path and i.snippet})


def _cap(sanitizer: Sanitizer, value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    return sanitizer.text(value, limit=limit)


def _finding_instance(finding: Finding, sanitizer: Sanitizer) -> Instance:
    location = finding.location
    return Instance(
        finding_id=finding.id,
        message=sanitizer.text(finding.message),
        source="finding",
        kind=finding.kind.value,
        verification=finding.verification.value,
        path=sanitizer.text(location.path) if location else None,
        line=location.line if location else None,
        end_line=location.end_line if location else None,
        provider=finding.provider,
    )


def _check_instance(check: CheckResult, sanitizer: Sanitizer) -> Instance:
    return Instance(
        finding_id=check.id,
        message=sanitizer.text(check.message),
        source="check",
        kind="check",
        verification=check.verification.value,
        status=check.status.value,
        scenario_id=check.scenario_id,
        expected=_cap(sanitizer, check.expected, METADATA_FIELD_CHARS),
        observed=_cap(sanitizer, check.observed, METADATA_FIELD_CHARS),
    )


def _read_lines(repo: Path, rel_path: str) -> list[str] | None:
    try:
        target = resolve_inside(repo, rel_path)
    except PathOutsideRootError:
        return None
    try:
        if not target.is_file() or target.stat().st_size > MAX_SOURCE_BYTES:
            return None
        raw = target.read_bytes()
    except OSError:
        return None
    return raw.decode("utf-8", errors="replace").splitlines()


def _snippet(lines: list[str], line: int, end_line: int | None) -> tuple[int, str]:
    first_hit = max(1, line)
    last_hit = max(first_hit, end_line or first_hit)
    start = max(1, first_hit - SNIPPET_RADIUS)
    end = min(len(lines), last_hit + SNIPPET_RADIUS)
    end = min(end, start + SNIPPET_MAX_LINES - 1)
    return start, "\n".join(lines[start - 1 : end])


def _traffic_bodies(
    check: CheckResult, exchanges: Mapping[tuple[str | None, int], RecordedExchange]
) -> list[dict[str, Any]]:
    bodies: list[dict[str, Any]] = []
    for evidence in check.evidence:
        if evidence.type != "traffic" or evidence.exchange_seq is None:
            continue
        exchange = exchanges.get((check.scenario_id, evidence.exchange_seq))
        if exchange is not None and exchange.request_json is not None:
            bodies.append({"seq": exchange.seq, "request_json": exchange.request_json})
    return bodies


def select(
    report: Report,
    *,
    level: ContextLevel = "metadata",
    max_groups: int = 5,
    repo: str | os.PathLike[str] | None = None,
    catalogue: CheckCatalogue | None = None,
    sanitizer: Sanitizer | None = None,
) -> Selection:
    """Select, group, order and enrich what ``explain`` may send. Reads files only at the
    ``snippets`` level, and only inside ``repo``."""
    catalogue = catalogue or {}
    repo_path = Path(repo if repo is not None else ".")
    sanitizer = sanitizer or Sanitizer(repo_path)
    mirrored = {f.check_ref for f in report.findings if f.check_ref}

    groups: dict[str, Group] = {}
    checks_by_instance: dict[str, CheckResult] = {}

    def group_for(rule_id: str, title: str, severity: Severity, remediation: str) -> Group:
        group = groups.get(rule_id)
        if group is None:
            group = groups[rule_id] = Group(
                rule_id=rule_id,
                title=sanitizer.text(title),
                severity=severity,
                remediation=sanitizer.text(remediation),
                total=0,
            )
        elif severity.rank > group.severity.rank:
            group.severity = severity
        if not group.remediation and remediation:
            group.remediation = sanitizer.text(remediation)
        return group

    for finding in report.findings:
        group = group_for(finding.rule_id, finding.title, finding.severity, finding.remediation)
        group.total += 1
        if len(group.instances) < INSTANCES_PER_GROUP:
            group.instances.append(_finding_instance(finding, sanitizer))
    for scenario in report.scenarios:
        for check in scenario.checks:
            wanted = check.status is CheckStatus.NEEDS_REVIEW or (
                check.status is CheckStatus.FAILED and check.id not in mirrored
            )
            if not wanted:
                continue
            title, remediation = catalogue.get(check.check_id, (check.check_id, ""))
            group = group_for(check.check_id, title, check.severity, remediation)
            group.total += 1
            if len(group.instances) < INSTANCES_PER_GROUP:
                group.instances.append(_check_instance(check, sanitizer))
                checks_by_instance[check.id] = check
    # Runtime findings point at their check: its traffic is theirs too (snippets level).
    checks_by_id = {c.id: c for s in report.scenarios for c in s.checks}
    for finding in report.findings:
        if finding.check_ref and finding.check_ref in checks_by_id:
            checks_by_instance.setdefault(finding.id, checks_by_id[finding.check_ref])

    ordered = sorted(groups.values(), key=lambda g: (-g.severity.rank, -g.total, g.rule_id))
    kept = ordered[:max_groups]
    selection = Selection(level=level, groups=kept, dropped_groups=len(ordered) - len(kept))
    if level == "snippets":
        _add_snippets(selection, repo_path, sanitizer)
        _add_traffic(selection, report, checks_by_instance, sanitizer)
    return selection


def _add_snippets(selection: Selection, repo: Path, sanitizer: Sanitizer) -> None:
    budget = SNIPPET_TOTAL_CHARS
    cache: dict[str, list[str] | None] = {}
    missing: set[str] = set()
    for group in selection.groups:
        for instance in group.instances:
            if not instance.path or instance.line is None or instance.line < 1:
                continue
            if instance.path not in cache:
                cache[instance.path] = _read_lines(repo, instance.path)
            lines = cache[instance.path]
            if lines is None:
                if instance.path not in missing:
                    missing.add(instance.path)
                    selection.notes.append(
                        f"snippet omitted: {instance.path} was not found under --repo "
                        f"{Sanitizer(None).paths(str(repo))}"
                    )
                continue
            start, text = _snippet(lines, instance.line, instance.end_line)
            # §19.3's snippet limits (40 lines, 12 000 characters in total) replace the
            # generic 2 000-character string cap for source lines (ADR-031).
            text = sanitizer.text(text, limit=None)
            if len(text) > budget:
                selection.notes.append(
                    f"snippet omitted for {instance.finding_id}: the "
                    f"{SNIPPET_TOTAL_CHARS}-character snippet budget is used up"
                )
                continue
            budget -= len(text)
            instance.snippet = text
            instance.snippet_start_line = start


def _add_traffic(
    selection: Selection,
    report: Report,
    checks_by_instance: Mapping[str, CheckResult],
    sanitizer: Sanitizer,
) -> None:
    exchanges = {(s.scenario_id, e.seq): e for s in report.scenarios for e in s.exchanges}
    for group in selection.groups:
        for instance in group.instances:
            check = checks_by_instance.get(instance.finding_id)
            if check is None:
                continue
            bodies = _traffic_bodies(check, exchanges)
            if bodies:
                # Bodies only: recorded headers are never part of the excerpt (§19.4).
                text = sanitizer.json_text(bodies)
                instance.traffic_excerpt = sanitizer.text(text, limit=TRAFFIC_CHARS)
