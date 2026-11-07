"""Report data models (schema 1.0). Pure data: no I/O in this module.

Statuses are never collapsed: findings carry a kind and a verification level; checks carry one
of six statuses; AI output lives in its own list and is never counted with findings or checks.

Two fields are deliberately loose until the phase that owns their model exists:

- ``Report.capability_profile``   -> ``CapabilityProfile`` (Phase 6, providers)
- ``Report.ai_suggestions``       -> ``AISuggestion`` / ``AIExplanation`` (Phase 8, diagnostics)
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "1.0"
EXCERPT_LIMIT = 2000


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- Enumerations (§21.1) -------------------------------------------------------------------


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

    @property
    def rank(self) -> int:
        """Higher is more severe: info=0 … critical=4."""
        return _SEVERITY_RANK[self]


_SEVERITY_RANK = {
    Severity.INFO: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class FindingKind(StrEnum):
    STATIC = "static"
    RUNTIME = "runtime"
    HYPOTHESIS = "hypothesis"


class Verification(StrEnum):
    STATIC = "static"  # source pattern match
    RUNTIME_MOCK = "runtime_mock"  # observed in recorded traffic against the contract mock
    RUNTIME_LIVE = "runtime_live"  # observed against the real provider (probe)


class CheckStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    UNSUPPORTED = "unsupported"
    NEEDS_REVIEW = "needs_review"
    ERROR = "error"


# Worst first: failed > error > needs_review > passed > unsupported > skipped.
_STATUS_SEVERITY_ORDER = (
    CheckStatus.FAILED,
    CheckStatus.ERROR,
    CheckStatus.NEEDS_REVIEW,
    CheckStatus.PASSED,
    CheckStatus.UNSUPPORTED,
    CheckStatus.SKIPPED,
)


def worst_status(statuses: Iterable[CheckStatus]) -> CheckStatus | None:
    """Scenario status = worst of its checks; ``None`` when there are no checks."""
    present = set(statuses)
    for status in _STATUS_SEVERITY_ORDER:
        if status in present:
            return status
    return None


# --- Core models (§21.3) --------------------------------------------------------------------


class Location(_Model):
    path: str  # POSIX, relative to the scan root
    line: int = Field(ge=0)
    column: int = Field(default=0, ge=0)
    end_line: int | None = None


class Evidence(_Model):
    type: Literal["source", "traffic", "process", "documentation", "probe"]
    location: Location | None = None
    exchange_seq: int | None = None  # RecordedExchange.seq
    excerpt: str = ""  # producers redact; truncated here to 2 000 characters
    note: str = ""

    @field_validator("excerpt")
    @classmethod
    def _limit_excerpt(cls, value: str) -> str:
        if len(value) > EXCERPT_LIMIT:
            return value[: EXCERPT_LIMIT - 1] + "…"
        return value


class Finding(_Model):
    id: str  # Finding.make_id(...): stable across runs
    rule_id: str
    title: str
    kind: FindingKind
    verification: Verification
    severity: Severity
    confidence: Confidence
    message: str
    location: Location | None
    evidence: list[Evidence]
    remediation: str
    doc_url: str | None = None
    provider: str | None = None  # detected provider for this call site
    check_ref: str | None = None  # CheckResult.id for runtime findings

    @staticmethod
    def make_id(
        rule_id: str,
        path: str | None = None,
        line: int | None = None,
        col: int | None = None,
        check_ref: str | None = None,
    ) -> str:
        """``"f-"`` + first 10 hex chars of sha1(rule_id|path|line|col|check_ref)."""
        parts = [rule_id, path, line, col, check_ref]
        key = "|".join("" if part is None else str(part) for part in parts)
        return "f-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]


class CheckResult(_Model):
    id: str  # "<scenario>:<check_id>", e.g. "S04:FC-RT-012"
    check_id: str
    scenario_id: str | None
    status: CheckStatus
    severity: Severity
    verification: Verification
    message: str
    reason: str | None = None  # for skipped / unsupported
    expected: str | None = None
    observed: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def _passed_needs_evidence(self) -> CheckResult:
        if self.status is CheckStatus.PASSED and not self.evidence:
            raise ValueError("a passed check must carry evidence")
        return self


class Endpoint(StrEnum):
    """API endpoint a request targeted (values match the scanner's call-site endpoints)."""

    RESPONSES = "responses"
    CHAT_COMPLETIONS = "chat.completions"
    MODELS = "models"


class RecordedExchange(_Model):
    """One request seen by the mock and how it was answered (plan §17.3).

    Written by ``mock.recorder.TrafficRecorder``; every string in it is already redacted.
    ``request_headers["authorization"]`` is ``"[FAKE KEY]"`` when it carried the configured fake
    key, ``"[REDACTED]"`` for any other value, and absent when the request sent none.
    """

    seq: int = Field(ge=1)
    at_ms: int = Field(ge=0)  # monotonic ms since the recorder started
    method: str
    path: str  # e.g. "/api/model/v1/responses"
    endpoint: Endpoint | None
    request_headers: dict[str, str]
    request_json: dict[str, Any] | None  # redacted; None when the body was not a JSON object
    request_sha256: str  # canonical JSON hash (sorted keys) for retry detection
    stream: bool
    step_id: str  # which scripted step served it (or the mock's own guard, e.g. "auth")
    response_status: int
    response_summary: dict[str, Any] = Field(default_factory=dict)


class AppRun(_Model):
    command: list[str]  # redacted
    exit_code: int | None
    timed_out: bool
    duration_ms: int
    stdout_excerpt: str  # redacted, ≤ 8 KB
    stderr_excerpt: str


class ScenarioResult(_Model):
    scenario_id: str
    name: str
    status: CheckStatus  # worst_status(check.status for check in checks)
    app_run: AppRun | None
    exchanges: list[RecordedExchange] = Field(default_factory=list)
    checks: list[CheckResult] = Field(default_factory=list)


class ProviderDetection(_Model):
    provider: Literal[
        "bharatcode", "openai", "openai_compatible_unknown", "openai_or_env", "unknown"
    ]
    confidence: Confidence
    location: Location
    signals: list[str]  # e.g. ["base_url literal contains bharatcode.ai"]


class Diagnostic(_Model):
    """Tool-level issue (parse error, file too large, limit reached)."""

    level: Literal["info", "warning", "error"]
    code: str
    message: str
    path: str | None = None


class RepoMetadata(_Model):
    root_name: str  # basename only: no absolute paths in reports
    python_files_scanned: int
    files_skipped: dict[str, int]  # reason -> count
    total_bytes_scanned: int


class ReportMetadata(_Model):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    tool_version: str
    generated_at: datetime  # timezone-aware, UTC; reporters convert to local time
    command: Literal["scan", "test", "explain", "ci", "probe"]
    options: dict[str, Any]  # redacted
    python_version: str
    platform: str
    mock_fidelity: Literal["documentation_model", "compared_with_live", "n/a"]

    @field_validator("generated_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("generated_at must be timezone-aware (use datetime.now(UTC))")
        return value


_E = TypeVar("_E", bound=StrEnum)


def _count_in_order(values: Iterable[_E], order: Iterable[_E]) -> dict[_E, int]:
    counts = Counter(values)
    return {member: counts[member] for member in order if counts[member]}


class Summary(_Model):
    findings_by_severity: dict[Severity, int]
    findings_by_kind: dict[FindingKind, int]
    checks_by_status: dict[CheckStatus, int]
    checks_by_verification: dict[Verification, int]
    ai_suggestions: int
    highest_severity: Severity | None

    @classmethod
    def from_report_parts(
        cls,
        findings: Iterable[Finding] = (),
        scenarios: Iterable[ScenarioResult] = (),
        ai_suggestions: int = 0,
    ) -> Summary:
        """Count findings and checks. Only non-zero counts are listed, in enum order.

        ``highest_severity`` covers every finding, hypotheses included; CI gating
        (``--fail-on``) applies its own hypothesis rule.
        """
        findings = list(findings)
        checks = [check for scenario in scenarios for check in scenario.checks]
        highest = max((f.severity for f in findings), key=lambda s: s.rank, default=None)
        return cls(
            findings_by_severity=_count_in_order((f.severity for f in findings), Severity),
            findings_by_kind=_count_in_order((f.kind for f in findings), FindingKind),
            checks_by_status=_count_in_order((c.status for c in checks), CheckStatus),
            checks_by_verification=_count_in_order((c.verification for c in checks), Verification),
            ai_suggestions=ai_suggestions,
            highest_severity=highest,
        )


class Report(_Model):
    metadata: ReportMetadata
    repo: RepoMetadata | None
    providers_detected: list[ProviderDetection] = Field(default_factory=list)
    # Placeholder until Phase 6 defines CapabilityProfile; Phase 6 must tighten this type.
    capability_profile: dict[str, Any] | None = None
    findings: list[Finding] = Field(default_factory=list)
    scenarios: list[ScenarioResult] = Field(default_factory=list)
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    # Placeholder until Phase 8 defines AISuggestion; Phase 8 must tighten this type.
    ai_suggestions: list[dict[str, Any]] = Field(default_factory=list)
    summary: Summary


# --- Requests (§21.4) -----------------------------------------------------------------------


class ScanRequest(_Model):
    """Input to ``orchestrator.run_scan``. Not part of the report schema."""

    root: Path
    provider_override: str | None = None  # "bharatcode" | "openai_compatible" | None (auto)
    rule_ids: list[str] | None = None
    min_severity: Severity = Severity.INFO
