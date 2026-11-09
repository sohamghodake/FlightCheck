"""Test doubles for the diagnostics tests (imported by the tests in this directory).

Report builders, and the in-process mock behind an ``httpx2.MockTransport`` (the
``tests/providers/provider_bridge.py`` pattern; not imported across test directories). No
sockets and no real network.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2
from fastapi.testclient import TestClient

from flightcheck.core.config import FlightCheckConfig, load_config
from flightcheck.core.models import (
    CheckResult,
    CheckStatus,
    Confidence,
    Evidence,
    Finding,
    FindingKind,
    Location,
    RecordedExchange,
    RepoMetadata,
    Report,
    ReportMetadata,
    ScenarioResult,
    Severity,
    Summary,
    Verification,
    worst_status,
)
from flightcheck.mock.app import create_mock_app
from flightcheck.mock.faults import FaultScript, MockAction, MockStep
from flightcheck.mock.recorder import TrafficRecorder
from flightcheck.orchestrator import explain_service

FAKE_KEY = "fc-test-key-not-a-secret"
FIXED_NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
_SKIP_REQUEST_HEADERS = {"host", "content-length", "transfer-encoding"}


# --- Reports --------------------------------------------------------------------------------


def finding(
    rule_id: str = "FC-BC-001",
    *,
    severity: Severity = Severity.HIGH,
    path: str | None = "app/chat.py",
    line: int = 10,
    message: str = "store=True on /responses",
    kind: FindingKind = FindingKind.STATIC,
    check_ref: str | None = None,
    title: str | None = None,
    remediation: str = "Send store=false.",
) -> Finding:
    verification = (
        Verification.STATIC if kind is not FindingKind.RUNTIME else (Verification.RUNTIME_MOCK)
    )
    return Finding(
        id=Finding.make_id(rule_id, path, line, 0, check_ref),
        rule_id=rule_id,
        title=title or f"Title of {rule_id}",
        kind=kind,
        verification=verification,
        severity=severity,
        confidence=Confidence.HIGH,
        message=message,
        location=Location(path=path, line=line) if path else None,
        evidence=[],
        remediation=remediation,
        provider="bharatcode",
        check_ref=check_ref,
    )


def check(
    check_id: str = "FC-RT-020",
    scenario_id: str = "S03",
    *,
    status: CheckStatus = CheckStatus.NEEDS_REVIEW,
    severity: Severity = Severity.MEDIUM,
    message: str = "No expectation configured.",
    expected: str | None = None,
    observed: str | None = None,
    exchange_seq: int | None = None,
) -> CheckResult:
    evidence = [Evidence(type="process", excerpt="exit 1", note="app run")]
    if exchange_seq is not None:
        evidence.append(Evidence(type="traffic", exchange_seq=exchange_seq, excerpt="POST"))
    return CheckResult(
        id=f"{scenario_id}:{check_id}",
        check_id=check_id,
        scenario_id=scenario_id,
        status=status,
        severity=severity,
        verification=Verification.RUNTIME_MOCK,
        message=message,
        expected=expected,
        observed=observed,
        evidence=evidence,
    )


def exchange(seq: int, request_json: dict[str, Any], headers: dict[str, str] | None = None):
    return RecordedExchange(
        seq=seq,
        at_ms=seq,
        method="POST",
        path="/api/model/v1/responses",
        endpoint="responses",
        request_headers=headers or {"authorization": "[FAKE KEY]"},
        request_json=request_json,
        request_sha256="0" * 64,
        stream=False,
        step_id="s",
        response_status=200,
    )


def scenario(
    scenario_id: str, checks: list[CheckResult], exchanges: Iterable[RecordedExchange] = ()
) -> ScenarioResult:
    return ScenarioResult(
        scenario_id=scenario_id,
        name=f"scenario {scenario_id}",
        status=worst_status(c.status for c in checks) or CheckStatus.SKIPPED,
        app_run=None,
        exchanges=list(exchanges),
        checks=checks,
    )


def report(
    findings: Iterable[Finding] = (),
    scenarios: Iterable[ScenarioResult] = (),
    command: str = "scan",
) -> Report:
    findings = list(findings)
    scenarios = list(scenarios)
    return Report(
        metadata=ReportMetadata(
            tool_version="0.1.0",
            generated_at=FIXED_NOW,
            command=command,  # type: ignore[arg-type]
            options={},
            python_version="3.12.0",
            platform="test",
            mock_fidelity="n/a",
        ),
        repo=RepoMetadata(
            root_name="app", python_files_scanned=1, files_skipped={}, total_bytes_scanned=10
        ),
        findings=findings,
        scenarios=scenarios,
        summary=Summary.from_report_parts(findings, scenarios),
    )


def deterministic_part(rep: Report) -> str:
    """Everything except the AI output (ADR-019), byte for byte."""
    data = json.loads(rep.model_dump_json())
    data.pop("ai_suggestions")
    data["summary"].pop("ai_suggestions")
    return json.dumps(data, sort_keys=True)


# --- Config and service ---------------------------------------------------------------------


def config(root: Path, *, key: str | None = FAKE_KEY, **explain: Any) -> FlightCheckConfig:
    environ = {"BHARATCODE_API_KEY": key} if key else {}
    return load_config(root, {"explain": explain} if explain else None, environ=environ)


class Sleeps(list[float]):
    def __call__(self, seconds: float) -> None:
        self.append(seconds)


def service(cfg: FlightCheckConfig, transport: httpx2.BaseTransport | None = None, **kw: Any):
    kw.setdefault("now", lambda: FIXED_NOW)
    kw.setdefault("sleep", Sleeps())
    return explain_service(cfg, transport=transport, **kw)


# --- Mock transport -------------------------------------------------------------------------


@dataclass
class Bridge:
    transport: httpx2.MockTransport
    recorder: TrafficRecorder
    requests: list[httpx2.Request] = field(default_factory=list)

    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(request.content) for request in self.requests]


def mock_transport(script: FaultScript | None = None) -> Bridge:
    recorder = TrafficRecorder()
    client = TestClient(create_mock_app(script or FaultScript(), recorder, fake_key=FAKE_KEY))
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        headers = {
            name: value
            for name, value in request.headers.items()
            if name.lower() not in _SKIP_REQUEST_HEADERS
        }
        answer = client.request(
            request.method,
            request.url.raw_path.decode("ascii"),
            content=request.content,
            headers=headers,
        )
        out_headers = [
            (name, value)
            for name, value in answer.headers.multi_items()
            if name.lower() not in {"content-length", "transfer-encoding"}
        ]
        return httpx2.Response(answer.status_code, headers=out_headers, content=answer.content)

    return Bridge(httpx2.MockTransport(handler), recorder, seen)


def steps(*actions: MockAction | tuple[MockAction, dict[str, Any]]) -> FaultScript:
    built = []
    for index, action in enumerate(actions):
        if isinstance(action, tuple):
            built.append(MockStep(id=f"s{index}", action=action[0], params=action[1]))
        else:
            built.append(MockStep(id=f"s{index}", action=action))
    return FaultScript(steps=built)


def batch_json(*explanations: dict[str, Any]) -> str:
    return json.dumps({"explanations": list(explanations)})


def explanation(rule_id: str, finding_ids: list[str], **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "rule_id": rule_id,
        "finding_ids": finding_ids,
        "summary": f"Summary for {rule_id}",
        "root_cause": "Root cause.",
        "impact": "Impact.",
        "fix_steps": ["Do the documented thing."],
        "patch": None,
        "caveats": [],
        "model_stated_confidence": "medium",
    }
    data.update(overrides)
    return data


def ok_text(text: str) -> tuple[MockAction, dict[str, Any]]:
    return (MockAction.OK_TEXT, {"text": text})
