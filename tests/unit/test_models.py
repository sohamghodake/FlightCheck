from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from flightcheck import __version__
from flightcheck.core.models import (
    EXCERPT_LIMIT,
    CheckResult,
    CheckStatus,
    Confidence,
    Evidence,
    Finding,
    FindingKind,
    Location,
    Report,
    ReportMetadata,
    ScenarioResult,
    Severity,
    Summary,
    Verification,
    worst_status,
)


def _evidence() -> Evidence:
    return Evidence(type="traffic", exchange_seq=2, excerpt='{"store": false}')


def _check(status: CheckStatus, check_id: str = "FC-RT-001", **extra) -> CheckResult:
    return CheckResult(
        id=f"S01:{check_id}",
        check_id=check_id,
        scenario_id="S01",
        status=status,
        severity=Severity.HIGH,
        verification=Verification.RUNTIME_MOCK,
        message="m",
        **extra,
    )


def _finding(rule_id: str, severity: Severity, kind: FindingKind = FindingKind.STATIC) -> Finding:
    location = Location(path="app.py", line=12, column=4)
    return Finding(
        id=Finding.make_id(rule_id, location.path, location.line, location.column),
        rule_id=rule_id,
        title="t",
        kind=kind,
        verification=Verification.STATIC,
        severity=severity,
        confidence=Confidence.HIGH,
        message="m",
        location=location,
        evidence=[Evidence(type="source", location=location, excerpt="store=True")],
        remediation="r",
    )


def _metadata(**extra) -> ReportMetadata:
    values = {
        "tool_version": __version__,
        "generated_at": datetime(2026, 11, 6, 15, 0, tzinfo=UTC),
        "command": "scan",
        "options": {},
        "python_version": "3.12.14",
        "platform": "darwin",
        "mock_fidelity": "n/a",
    }
    values.update(extra)
    return ReportMetadata(**values)


def test_passed_check_without_evidence_raises() -> None:
    with pytest.raises(ValidationError, match="a passed check must carry evidence"):
        _check(CheckStatus.PASSED, evidence=[])


def test_passed_check_with_evidence_is_valid() -> None:
    assert _check(CheckStatus.PASSED, evidence=[_evidence()]).status is CheckStatus.PASSED


@pytest.mark.parametrize(
    "status",
    [s for s in CheckStatus if s is not CheckStatus.PASSED],
)
def test_other_statuses_do_not_need_evidence(status: CheckStatus) -> None:
    assert _check(status).evidence == []


def test_make_id_is_stable_and_shaped() -> None:
    first = Finding.make_id("FC-BC-001", "app.py", 12, 4)
    assert first == Finding.make_id("FC-BC-001", "app.py", 12, 4)
    assert first.startswith("f-") and len(first) == 12
    int(first[2:], 16)  # hex


def test_make_id_differs_by_each_part() -> None:
    base = Finding.make_id("FC-BC-001", "app.py", 12, 4, None)
    assert base != Finding.make_id("FC-BC-002", "app.py", 12, 4, None)
    assert base != Finding.make_id("FC-BC-001", "b.py", 12, 4, None)
    assert base != Finding.make_id("FC-BC-001", "app.py", 13, 4, None)
    assert base != Finding.make_id("FC-BC-001", "app.py", 12, 5, None)
    assert base != Finding.make_id("FC-BC-001", "app.py", 12, 4, "S04:FC-RT-012")


def test_models_forbid_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        Location(path="a.py", line=1, colour="red")


def test_evidence_excerpt_is_truncated() -> None:
    evidence = Evidence(type="source", excerpt="x" * (EXCERPT_LIMIT + 50))
    assert len(evidence.excerpt) == EXCERPT_LIMIT
    assert evidence.excerpt.endswith("…")


def test_worst_status_order() -> None:
    assert worst_status([CheckStatus.SKIPPED, CheckStatus.PASSED]) is CheckStatus.PASSED
    assert worst_status([CheckStatus.PASSED, CheckStatus.NEEDS_REVIEW]) is CheckStatus.NEEDS_REVIEW
    assert worst_status([CheckStatus.ERROR, CheckStatus.FAILED]) is CheckStatus.FAILED
    assert worst_status([CheckStatus.UNSUPPORTED, CheckStatus.SKIPPED]) is CheckStatus.UNSUPPORTED
    assert worst_status([]) is None


def test_severity_rank() -> None:
    assert Severity.CRITICAL.rank > Severity.HIGH.rank > Severity.MEDIUM.rank
    assert Severity.LOW.rank > Severity.INFO.rank


def test_summary_from_report_parts_counts_without_collapsing() -> None:
    findings = [
        _finding("FC-BC-001", Severity.HIGH),
        _finding("FC-BC-002", Severity.HIGH),
        _finding("FC-BC-007", Severity.MEDIUM, FindingKind.HYPOTHESIS),
    ]
    scenario = ScenarioResult(
        scenario_id="S01",
        name="baseline",
        status=CheckStatus.FAILED,
        app_run=None,
        checks=[
            _check(CheckStatus.PASSED, "FC-RT-001", evidence=[_evidence()]),
            _check(CheckStatus.FAILED, "FC-RT-002"),
            _check(CheckStatus.NEEDS_REVIEW, "FC-RT-003"),
        ],
    )
    summary = Summary.from_report_parts(findings, [scenario], ai_suggestions=2)
    assert summary.findings_by_severity == {Severity.HIGH: 2, Severity.MEDIUM: 1}
    assert summary.findings_by_kind == {FindingKind.STATIC: 2, FindingKind.HYPOTHESIS: 1}
    assert summary.checks_by_status == {
        CheckStatus.PASSED: 1,
        CheckStatus.FAILED: 1,
        CheckStatus.NEEDS_REVIEW: 1,
    }
    assert summary.checks_by_verification == {Verification.RUNTIME_MOCK: 3}
    assert summary.ai_suggestions == 2
    assert summary.highest_severity is Severity.HIGH


def test_empty_summary() -> None:
    summary = Summary.from_report_parts()
    assert summary.highest_severity is None
    assert summary.findings_by_severity == {}


def test_report_metadata_requires_timezone() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        _metadata(generated_at=datetime(2026, 11, 6, 15, 0))


def test_report_json_round_trip() -> None:
    findings = [_finding("FC-BC-001", Severity.HIGH)]
    report = Report(
        metadata=_metadata(),
        repo=None,
        findings=findings,
        summary=Summary.from_report_parts(findings),
    )
    data = report.model_dump(mode="json")
    assert data["metadata"]["schema_version"] == "1.0"
    assert data["findings"][0]["severity"] == "high"
    assert data["summary"]["findings_by_severity"] == {"high": 1}
    assert data["capability_profile"] is None
    assert data["ai_suggestions"] == []
    assert Report.model_validate_json(report.model_dump_json()) == report
