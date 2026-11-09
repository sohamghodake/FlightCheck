"""HTML report (plan §24, §27 Phase 9; ADR-021, ADR-032)."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import pytest

from flightcheck.core.capabilities import (
    Capability,
    CapabilityEntry,
    CapabilityProfile,
    SupportLevel,
)
from flightcheck.core.models import (
    AI_LABEL,
    AIExplanation,
    AISuggestion,
    AppRun,
    CheckResult,
    CheckStatus,
    Confidence,
    Diagnostic,
    Evidence,
    Finding,
    FindingKind,
    Location,
    ProviderDetection,
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
from flightcheck.reporting import html_report
from flightcheck.reporting.html_report import (
    MESSAGE_LIMIT,
    build_view,
    render_html,
    write_html,
)
from flightcheck.reporting.json_report import report_to_json

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
SCRIPT = "<script>alert(1)</script>"
BC_KEY = "bc_live_" + "PLANTEDHTML1111111111"
SK_KEY = "sk-" + "PLANTEDHTML2222222222"
BEARER = "Bearer " + "PLANTEDHTML3333333333"
SECTION_IDS = (
    "metadata",
    "summary",
    "providers",
    "findings",
    "scenarios",
    "ai",
    "diagnostics",
    "limitations",
)


# --- builders -------------------------------------------------------------------------------


def _finding(
    rule_id: str,
    *,
    path: str | None = "app/chat.py",
    line: int = 10,
    severity: Severity = Severity.HIGH,
    kind: FindingKind = FindingKind.STATIC,
    verification: Verification = Verification.STATIC,
    message: str = "store=True on /responses",
    excerpt: str = "client.responses.create(store=True)",
    doc_url: str | None = "https://bharatcode.ai/docs",
    check_ref: str | None = None,
) -> Finding:
    return Finding(
        id=Finding.make_id(rule_id, path, line, 0, check_ref),
        rule_id=rule_id,
        title=f"Title of {rule_id}",
        kind=kind,
        verification=verification,
        severity=severity,
        confidence=Confidence.MEDIUM,
        message=message,
        location=Location(path=path, line=line) if path else None,
        evidence=[
            Evidence(type="source", location=Location(path=path or ".", line=line), excerpt=excerpt)
        ],
        remediation="Send store=false.",
        doc_url=doc_url,
        provider="bharatcode",
        check_ref=check_ref,
    )


def _check(
    check_id: str,
    status: CheckStatus,
    *,
    scenario_id: str = "S01",
    verification: Verification = Verification.RUNTIME_MOCK,
    message: str | None = None,
) -> CheckResult:
    evidence = [Evidence(type="traffic", exchange_seq=1, excerpt='{"store": false}')]
    return CheckResult(
        id=f"{scenario_id}:{check_id}",
        check_id=check_id,
        scenario_id=scenario_id,
        status=status,
        severity=Severity.MEDIUM,
        verification=verification,
        message=message or f"{check_id} is {status.value}",
        reason="precondition not met" if status is CheckStatus.SKIPPED else None,
        expected="store=false",
        observed="store=false",
        evidence=evidence,
    )


def _exchange(seq: int, body: dict[str, Any] | None = None) -> RecordedExchange:
    return RecordedExchange(
        seq=seq,
        at_ms=seq,
        method="POST",
        path="/api/model/v1/responses",
        endpoint="responses",
        request_headers={"authorization": "[FAKE KEY]"},
        request_json=body or {"model": "qwen-3.8-27b", "store": False, "input": "hi"},
        request_sha256="0" * 64,
        stream=False,
        step_id="ok",
        response_status=200,
        response_summary={"status": "completed"},
    )


def _scenario(
    sid: str, checks: list[CheckResult], exchanges: list[RecordedExchange] | None = None
) -> ScenarioResult:
    return ScenarioResult(
        scenario_id=sid,
        name=f"scenario {sid}",
        status=worst_status(c.status for c in checks) or CheckStatus.SKIPPED,
        app_run=AppRun(
            command=["python", "main.py"],
            exit_code=0,
            timed_out=False,
            duration_ms=120,
            stdout_excerpt="hello",
            stderr_excerpt="",
        ),
        exchanges=exchanges if exchanges is not None else [_exchange(1)],
        checks=checks,
    )


def _suggestion(rule_id: str, *, backend: str = "fake", **over: Any) -> AISuggestion:
    data: dict[str, Any] = {
        "rule_id": rule_id,
        "title": f"Title of {rule_id}",
        "severity": Severity.HIGH,
        "finding_ids": [Finding.make_id(rule_id, "app/chat.py", 10, 0, None)],
        "remediation": "Send store=false.",
        "explanation": AIExplanation(
            rule_id=rule_id,
            finding_ids=[Finding.make_id(rule_id, "app/chat.py", 10, 0, None)],
            summary="The call stores responses.",
            root_cause="store=True is not supported.",
            impact="Requests fail with 400.",
            fix_steps=["Set store=False."],
            caveats=["Check other call sites."],
            model_stated_confidence="medium",
        ),
        "backend": backend,
        "model": "fake-model" if backend == "fake" else "qwen-3.8-27b",
        "request_id": None,
        "structured_output": "json_schema",
        "latency_ms": 5,
        "diff": "--- a/app/chat.py\n+++ b/app/chat.py\n-store=True\n+store=False\n",
        "fallback_reason": None,
        "rejected_reason": None,
        "generated_at": NOW,
    }
    data.update(over)
    return AISuggestion(**data)


def _profile() -> CapabilityProfile:
    return CapabilityProfile(
        provider="bharatcode",
        base_url="https://bharatcode.ai/api/model/v1",
        documented_models=("qwen-3.8-27b",),
        max_output_tokens=32000,
        entries={
            Capability.RESPONSES_API: CapabilityEntry(
                level=SupportLevel.SUPPORTED_DOCUMENTED, source="https://bharatcode.ai/docs"
            ),
            Capability.STORE_TRUE: CapabilityEntry(
                level=SupportLevel.UNSUPPORTED_DOCUMENTED, source="docs", note="400"
            ),
        },
    )


def _report(
    findings: list[Finding] | None = None,
    scenarios: list[ScenarioResult] | None = None,
    ai: list[AISuggestion] | None = None,
    *,
    command: str = "scan",
    generated_at: datetime = NOW,
    options: dict[str, Any] | None = None,
    diagnostics: list[Diagnostic] | None = None,
    mock_fidelity: str = "n/a",
) -> Report:
    findings = findings or []
    scenarios = scenarios or []
    ai = ai or []
    return Report(
        metadata=ReportMetadata(
            tool_version="0.1.0",
            generated_at=generated_at,
            command=command,  # type: ignore[arg-type]
            options=options or {},
            python_version="3.12.0",
            platform="test",
            mock_fidelity=mock_fidelity,  # type: ignore[arg-type]
        ),
        repo=RepoMetadata(
            root_name="app", python_files_scanned=2, files_skipped={}, total_bytes_scanned=10
        ),
        providers_detected=[
            ProviderDetection(
                provider="bharatcode",
                confidence=Confidence.HIGH,
                location=Location(path=".", line=0),
                signals=["base_url literal contains bharatcode.ai"],
            )
        ],
        capability_profile=_profile(),
        findings=findings,
        scenarios=scenarios,
        diagnostics=diagnostics or [],
        ai_suggestions=ai,
        summary=Summary.from_report_parts(
            findings, scenarios, sum(1 for s in ai if s.explanation is not None)
        ),
    )


def _full_report(**over: Any) -> Report:
    """scan + test + explain content: every status, every verification level, a hypothesis,
    findings in two files and at repository level, traffic, diagnostics, AI entries."""
    statuses = list(CheckStatus)
    checks = [_check(f"FC-RT-0{10 + i}", st) for i, st in enumerate(statuses)]
    checks.append(_check("FC-RT-099", CheckStatus.PASSED, verification=Verification.RUNTIME_LIVE))
    findings = [
        _finding("FC-BC-001"),
        _finding("FC-GEN-004", line=20, severity=Severity.MEDIUM),
        _finding(
            "FC-BC-007", path="app/tools.py", severity=Severity.LOW, kind=FindingKind.HYPOTHESIS
        ),
        _finding("FC-GEN-005", path=None, severity=Severity.INFO),
        _finding(
            "FC-RT-010",
            path="app/tools.py",
            line=5,
            kind=FindingKind.RUNTIME,
            verification=Verification.RUNTIME_MOCK,
            check_ref="S01:FC-RT-011",
        ),
    ]
    ai = [
        _suggestion("FC-BC-001"),
        _suggestion("FC-GEN-004", backend="bharatcode", diff=None),
        _suggestion(
            "FC-GEN-005",
            backend="none",
            model=None,
            explanation=None,
            diff=None,
            structured_output="n/a",
            fallback_reason="no_key",
            remediation="Pass timeout= to the client.",
        ),
    ]
    data: dict[str, Any] = {
        "findings": findings,
        "scenarios": [_scenario("S01", checks, [_exchange(1), _exchange(2)])],
        "ai": ai,
        "command": "test",
        "options": {"fail_on": "high", "scenarios": ["S01"]},
        "diagnostics": [
            Diagnostic(
                level="warning", code="file_too_large", message="big.py skipped", path="big.py"
            )
        ],
        "mock_fidelity": "documentation_model",
    }
    data.update(over)
    return _report(**data)


def _section(html: str, section_id: str) -> str:
    match = re.search(rf'<section id="{section_id}".*?</section>', html, re.S)
    assert match, section_id
    return match.group(0)


# --- sections -------------------------------------------------------------------------------


def test_every_section_renders_for_an_empty_report():
    html = render_html(_report())
    for sid in SECTION_IDS:
        assert f'<section id="{sid}"' in html
    assert "<footer>" in html
    assert "No findings." in _section(html, "findings")
    assert "No runtime scenarios in this report." in _section(html, "scenarios")
    assert "No AI suggestions in this report." in _section(html, "ai")
    assert "No diagnostics." in _section(html, "diagnostics")
    assert html.rstrip().endswith("</html>")


def test_every_section_renders_for_a_full_report():
    html = render_html(_full_report())
    for sid in SECTION_IDS:
        assert f'<section id="{sid}"' in html
    findings = _section(html, "findings")
    assert findings.count('class="finding"') == 5
    for path in ("app/chat.py", "app/tools.py", "(repository)"):
        assert f"<code>{path}</code>" in findings
    assert "client.responses.create(store=True)" in findings  # the snippet
    scenarios = _section(html, "scenarios")
    assert 'id="scenario-S01"' in scenarios
    assert "Traffic: 2 recorded exchange(s)" in scenarios
    assert "<details>" in scenarios
    assert "qwen-3.8-27b" in scenarios  # request body excerpt
    providers = _section(html, "providers")
    assert "base_url literal contains bharatcode.ai" in providers
    assert "unsupported_documented" in providers
    assert "file_too_large" in _section(html, "diagnostics")
    assert "fail_on=" in _section(html, "metadata")


def test_report_without_scenarios_or_ai_does_not_crash():
    html = render_html(_report(findings=[_finding("FC-BC-001")]))
    assert "No runtime scenarios in this report." in html
    assert "No AI suggestions in this report." in html
    assert "FC-BC-001" in _section(html, "findings")


def test_report_without_repo_or_profile():
    rep = _report().model_copy(update={"repo": None, "capability_profile": None})
    html = render_html(rep)
    assert "No capability profile in this report." in html
    assert "<title>FlightCheck report</title>" in html


# --- statuses, verification levels, AI separation -------------------------------------------


def test_all_six_statuses_are_shown_distinctly():
    scenarios = _section(render_html(_full_report()), "scenarios")
    labels = {
        "passed": "passed",
        "failed": "failed",
        "needs_review": "needs review",
        "skipped": "skipped",
        "unsupported": "unsupported",
        "error": "error",
    }
    for status, label in labels.items():
        assert f'<span class="badge st-{status}">{label}</span>' in scenarios
    assert "Reason: precondition not met" in scenarios


def test_three_verification_levels_and_words():
    html = render_html(_full_report())
    for word in ("static", "observed with mock", "observed live", "AI suggestion"):
        assert word in html
    cards = _section(html, "summary")
    view = build_view(_full_report())["cards"]
    levels = {row["name"]: (row["findings"], row["checks"]) for row in view["verification"]}
    # 3 static findings (the hypothesis is kept separate), 1 runtime finding;
    # 6 mock checks and 1 live check.
    assert levels == {"static": (3, 0), "runtime_mock": (1, 6), "runtime_live": (0, 1)}
    assert view["hypotheses"] == 1
    assert "Hypotheses (unverified)" in cards
    statuses = {row["name"]: row["count"] for row in view["status"]}
    assert statuses == {
        "failed": 1,
        "error": 1,
        "needs_review": 1,
        "passed": 2,
        "unsupported": 1,
        "skipped": 1,
    }
    hypothesis = re.search(r'id="f-[0-9a-f]+">\s*<header>.*?FC-BC-007.*?</header>', html, re.S)
    assert hypothesis and "hypothesis (unverified)" in hypothesis.group(0)


def test_ai_entries_are_never_counted_in_the_summary_cards():
    with_ai = render_html(_full_report())
    without_ai = render_html(_full_report(ai=[]))
    assert _section(with_ai, "summary") == _section(without_ai, "summary")
    assert _section(with_ai, "findings") == _section(without_ai, "findings")
    assert _section(with_ai, "scenarios") == _section(without_ai, "scenarios")


def test_ai_section_labels_fake_fallback_and_diff():
    ai = _section(render_html(_full_report()), "ai")
    assert AI_LABEL in ai
    assert "1 explained" not in ai  # two explained entries, one fallback
    assert "2 explained, 1 fallback(s)." in ai
    assert "fake LLM, offline; not generated by BharatCode" in ai
    assert "AI suggestion (BharatCode qwen-3.8-27b) — not verified" in ai
    assert "<pre>--- a/app/chat.py" in ai
    assert "Fallback:</strong> no_key." in ai
    assert "No BHARATCODE_API_KEY is set" in ai  # the terminal's hint for the reason
    assert "Pass timeout= to the client." in ai  # deterministic remediation
    assert ai.count('class="ai"') == 3


def test_rejected_fallback_shows_reason():
    rep = _report(
        ai=[
            _suggestion(
                "FC-BC-001",
                explanation=None,
                diff=None,
                fallback_reason="rejected",
                rejected_reason="unknown finding id",
            )
        ]
    )
    ai = _section(render_html(rep), "ai")
    assert "rejected (unknown finding id)." in ai
    assert "0 explained, 1 fallback(s)." in ai


def test_limitations_and_mock_fidelity_notice():
    html = render_html(_full_report())
    limits = _section(html, "limitations")
    assert "One-hop name resolution only" in limits
    assert "Only literal values are inspected" in limits
    assert "models BharatCode&#39;s public documentation" in limits
    assert "models BharatCode&#39;s public documentation" in _section(html, "scenarios")
    static_only = _section(render_html(_report()), "limitations")
    assert "No runtime scenarios were run for this report" in static_only


# --- escaping, redaction, self-containment --------------------------------------------------


def test_script_is_escaped_everywhere():
    rep = _full_report(
        findings=[
            _finding("FC-BC-001", message=f"bad {SCRIPT}", excerpt=f"x = '{SCRIPT}'"),
            _finding("FC-BC-002", path=f"app/{SCRIPT}.py"),
        ],
        scenarios=[
            _scenario(
                "S01",
                [_check("FC-RT-010", CheckStatus.FAILED, message=f"saw {SCRIPT}")],
                [_exchange(1, {"input": SCRIPT})],
            )
        ],
        ai=[
            _suggestion(
                "FC-BC-001",
                explanation=AIExplanation(
                    rule_id="FC-BC-001",
                    finding_ids=[],
                    summary=f"summary {SCRIPT}",
                    root_cause="r",
                    impact="i",
                    fix_steps=[f"step {SCRIPT}"],
                    model_stated_confidence="low",
                ),
                diff=f"+{SCRIPT}\n",
            )
        ],
        options={"note": SCRIPT},
    )
    html = render_html(rep)
    assert "<script" not in html.lower()
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert html.count("&lt;script&gt;") >= 7


def test_unsafe_doc_url_is_not_linked():
    html = render_html(_report(findings=[_finding("FC-BC-001", doc_url="javascript:alert(1)")]))
    assert "javascript:" not in html
    assert 'href="https://bharatcode.ai/docs"' in render_html(
        _report(findings=[_finding("FC-BC-001")])
    )


def test_planted_secrets_never_appear():
    rep = _full_report(
        findings=[_finding("FC-SEC-001", message=f"key {BC_KEY}", excerpt=f'api_key="{SK_KEY}"')],
        scenarios=[
            _scenario(
                "S01",
                [_check("FC-RT-010", CheckStatus.FAILED, message=f"auth {BEARER}")],
                [_exchange(1, {"input": f"use {BC_KEY}", SK_KEY: "key as a dict key"})],
            )
        ],
        ai=[_suggestion("FC-BC-001", diff=f"+api_key = '{BC_KEY}'\n")],
        options={"token": SK_KEY},
    )
    rep.scenarios[0].app_run.stderr_excerpt = f"Authorization: {BEARER}"  # type: ignore[union-attr]
    html = render_html(rep)
    for secret in (BC_KEY, SK_KEY, BEARER, "PLANTEDHTML"):
        assert secret not in html
    assert "bc_live_…[REDACTED]" in html


def test_view_is_built_from_the_redacted_dump():
    view = build_view(_report(findings=[_finding("FC-SEC-001", message=f"k {BC_KEY}")]))
    assert BC_KEY not in repr(view)


def test_no_javascript_and_no_external_assets():
    html = render_html(_full_report())
    lowered = html.lower()
    for forbidden in ("<script", "<link", "src=", "@import", "url(http", "<iframe", "<object"):
        assert forbidden not in lowered, forbidden
    assert not re.search(r"<[^>]*\son[a-z]+\s*=", lowered)  # no inline event handlers
    assert "<style>" in html


def test_full_report_stays_under_two_megabytes():
    big_body = {"input": "x" * 6000, "model": "qwen-3.8-27b"}
    findings = [_finding("FC-BC-001", line=i, message="m" * 500) for i in range(1, 301)]
    scenarios = [
        _scenario(
            f"S0{n}",
            [
                _check(f"FC-RT-0{10 + i}", st, scenario_id=f"S0{n}")
                for i, st in enumerate(CheckStatus)
            ],
            [_exchange(seq, big_body) for seq in range(1, 21)],
        )
        for n in range(1, 7)
    ]
    ai = [_suggestion(f"FC-BC-00{i}") for i in range(1, 6)]
    html = render_html(_full_report(findings=findings, scenarios=scenarios, ai=ai))
    assert len(html.encode("utf-8")) < 2 * 1024 * 1024


def test_very_long_messages_are_wrapped_and_clipped():
    long = "word " * 5000
    html = render_html(_report(findings=[_finding("FC-BC-001", message=long)]))
    view = build_view(_report(findings=[_finding("FC-BC-001", message=long)]))
    shown = view["file_groups"][0]["findings"][0]["message"]
    assert len(shown) == MESSAGE_LIMIT and shown.endswith("…")
    assert "overflow-wrap: anywhere" in html


# --- determinism and writing ----------------------------------------------------------------


def test_output_is_deterministic_and_uses_generated_at():
    rep = _full_report()
    first = render_html(rep)
    assert first == render_html(rep)
    assert first == render_html(Report.model_validate_json(report_to_json(rep)))
    assert "at 2026-11-09 12:00:00 UTC" in first
    later = render_html(_full_report(generated_at=datetime(2030, 1, 2, 3, 4, 5, tzinfo=UTC)))
    assert "at 2030-01-02 03:04:05 UTC" in later
    assert "2026-11-09 12:00:00" not in later.split("<footer>")[1]


def test_autoescape_is_on():
    assert html_report._environment().autoescape is True


def test_write_html(tmp_path):
    target = write_html(_report(), tmp_path / "out" / "r.html")
    assert target.read_text(encoding="utf-8").startswith("<!doctype html>")


def test_write_html_refuses_a_directory(tmp_path):
    from flightcheck.core.errors import ConfigError

    with pytest.raises(ConfigError):
        write_html(_report(), tmp_path)


def test_write_html_refuses_a_symlink_outside(tmp_path):
    from flightcheck.core.errors import PathOutsideRootError

    outside = tmp_path / "outside.html"
    inner = tmp_path / "inner"
    inner.mkdir()
    (inner / "r.html").symlink_to(outside)
    with pytest.raises(PathOutsideRootError):
        write_html(_report(), inner / "r.html")
    assert not outside.exists()
