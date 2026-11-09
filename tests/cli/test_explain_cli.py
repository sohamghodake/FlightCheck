"""`flightcheck explain`: fake backend, dry run, consent, edge cases and exit codes."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx2
import pytest
from typer.testing import CliRunner

from flightcheck.cli import app as app_module
from flightcheck.cli import render as render_module
from flightcheck.cli.app import app
from flightcheck.core.models import (
    CheckResult,
    CheckStatus,
    Evidence,
    Finding,
    Report,
    ScenarioResult,
    Severity,
    Summary,
    Verification,
)
from flightcheck.diagnostics import service as service_module
from flightcheck.diagnostics.fake_llm import FAKE_PREFIX
from flightcheck.reporting.json_report import REPORT_FILENAME, read_report, report_to_json

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "tests" / "fixtures" / "apps" / "bc_bad_tool_pairing"
runner = CliRunner()
KEY = "bc_live_" + "cliTestKey123456"


def _deterministic(rep: Report) -> str:
    data = json.loads(rep.model_dump_json())
    data.pop("ai_suggestions")
    data["summary"].pop("ai_suggestions")
    return json.dumps(data, sort_keys=True)


@pytest.fixture
def scan_report(tmp_path: Path) -> Path:
    out = tmp_path / "out"
    result = runner.invoke(app, ["scan", str(APP), "--output", str(out), "--format", "json"])
    assert result.exit_code == 1, result.output
    return out / REPORT_FILENAME


@pytest.fixture
def no_send(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Adapter construction and every HTTP send fail loudly: proves nothing is sent."""
    calls: list[str] = []

    def no_adapter(*_a, **_k):
        calls.append("build_adapter")
        raise AssertionError("adapter must not be built")

    def no_http(*_a, **_k):
        calls.append("send")
        raise AssertionError("nothing may be sent")

    monkeypatch.setattr(service_module, "build_adapter", no_adapter)
    monkeypatch.setattr(httpx2.Client, "send", no_http)
    return calls


def _explain(*args: str, env: dict[str, str] | None = None, input: str | None = None):
    return runner.invoke(app, ["explain", *args], env=env, input=input)


def test_fake_backend_on_a_scan_report(scan_report: Path, no_send: list[str]) -> None:
    before = scan_report.read_bytes()
    result = _explain(str(scan_report), "--llm", "fake", "--yes", "--repo", str(APP))
    assert result.exit_code == 0, result.output
    assert "Destination: none" in result.stderr  # §20.5: printed on every run
    assert "AI suggestion (fake LLM" in result.stdout and FAKE_PREFIX in result.stdout
    assert "FC-GEN-007" in result.stdout
    explained_path = scan_report.with_name("flightcheck-report.explained.json")
    assert explained_path.is_file() and scan_report.read_bytes() == before
    original, explained = read_report(scan_report), read_report(explained_path)
    assert _deterministic(explained) == _deterministic(original)
    assert explained.metadata == original.metadata  # ADR-031: metadata unchanged
    assert explained.summary.ai_suggestions == len(explained.ai_suggestions) >= 1
    assert no_send == []


def test_json_format_prints_only_the_path(scan_report: Path, tmp_path: Path) -> None:
    target = tmp_path / "x" / "explained.json"
    result = _explain(
        str(scan_report), "--llm", "none", "--format", "json", "--output", str(target)
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.startswith("Explained report written: ")
    suggestion = read_report(target).ai_suggestions[0]
    assert suggestion.fallback_reason == "llm_none" and suggestion.remediation


def test_dry_run_prints_the_payload_and_sends_nothing(
    scan_report: Path, no_send: list[str]
) -> None:
    env = {"BHARATCODE_API_KEY": KEY}
    result = _explain(str(scan_report), "--dry-run", "--llm", "bharatcode", env=env)
    assert result.exit_code == 0, result.output
    assert "Request 1 of 1: POST https://bharatcode.ai/api/model/v1/responses" in result.stdout
    assert '"store": false' in result.stdout and '"max_output_tokens": 4000' in result.stdout
    assert "nothing was sent" in result.stdout
    assert KEY not in result.output and "Bearer" not in result.output
    assert "Send to BharatCode?" not in result.stderr  # no consent needed: nothing leaves
    assert not scan_report.with_name("flightcheck-report.explained.json").exists()
    assert no_send == []


def test_non_interactive_bharatcode_without_yes_exits_2(
    scan_report: Path, no_send: list[str]
) -> None:
    # CliRunner's stdin is not a TTY.
    result = _explain(str(scan_report), "--llm", "bharatcode", env={"BHARATCODE_API_KEY": KEY})
    assert result.exit_code == 2, result.output
    assert "not interactive" in result.stderr and "--yes" in result.stderr
    assert "Send to BharatCode?" in result.stderr  # the panel shows what would be sent
    assert "Secrets matching known patterns have been redacted" in result.stderr
    assert not scan_report.with_name("flightcheck-report.explained.json").exists()
    assert no_send == []
    assert KEY not in result.output


def test_default_backend_with_a_key_needs_consent_too(
    scan_report: Path, no_send: list[str]
) -> None:
    result = _explain(str(scan_report), env={"BHARATCODE_API_KEY": KEY})
    assert result.exit_code == 2, result.output
    assert "Destination: https://bharatcode.ai/api/model/v1/responses" in result.stderr
    assert no_send == []


def test_ci_true_counts_as_non_interactive(
    scan_report: Path, no_send: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    class TTY:
        @staticmethod
        def isatty() -> bool:
            return True

    monkeypatch.setattr(render_module, "sys", SimpleNamespace(stdin=TTY()))
    assert render_module.is_interactive({}) is True
    assert render_module.is_interactive({"CI": "true"}) is False
    monkeypatch.setenv("CI", "true")
    env = {"BHARATCODE_API_KEY": KEY}
    result = _explain(str(scan_report), "--llm", "bharatcode", env=env)
    assert result.exit_code == 2, result.output
    assert no_send == []


def test_interactive_decline_sends_nothing_and_falls_back(
    scan_report: Path, no_send: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_module, "is_interactive", lambda: True)
    env = {"BHARATCODE_API_KEY": KEY}
    result = _explain(str(scan_report), "--llm", "bharatcode", env=env, input="n\n")
    assert result.exit_code == 0, result.output
    assert "Proceed?" in result.stderr
    assert "consent_declined" in result.stdout
    explained = read_report(scan_report.with_name("flightcheck-report.explained.json"))
    assert {s.fallback_reason for s in explained.ai_suggestions} == {"consent_declined"}
    assert no_send == []


def test_yes_with_the_network_disabled_is_a_fallback_exit_0(scan_report: Path) -> None:
    # FLIGHTCHECK_NO_NETWORK=1 is set for every test: consent is given, nothing can be sent.
    env = {"BHARATCODE_API_KEY": KEY}
    result = _explain(str(scan_report), "--llm", "bharatcode", "--yes", env=env)
    assert result.exit_code == 0, result.output
    assert "network_disabled" in result.stdout and "FLIGHTCHECK_NO_NETWORK" in result.stdout
    explained = read_report(scan_report.with_name("flightcheck-report.explained.json"))
    assert {s.fallback_reason for s in explained.ai_suggestions} == {"network_disabled"}
    assert KEY not in result.output


def test_bharatcode_without_a_key_is_no_key_exit_0(scan_report: Path, monkeypatch) -> None:
    monkeypatch.delenv("BHARATCODE_API_KEY", raising=False)
    result = _explain(str(scan_report), "--llm", "bharatcode")
    assert result.exit_code == 0, result.output
    assert "no_key" in result.stdout and "Send to BharatCode?" not in result.stderr


def test_test_command_report_through_the_fake_backend(tmp_path: Path) -> None:
    failed = CheckResult(
        id="S04:FC-RT-012",
        check_id="FC-RT-012",
        scenario_id="S04",
        status=CheckStatus.FAILED,
        severity=Severity.HIGH,
        verification=Verification.RUNTIME_MOCK,
        message="call_id mismatch",
        evidence=[Evidence(type="traffic", exchange_seq=1, excerpt="x")],
    )
    review = CheckResult(
        id="S03:FC-RT-020",
        check_id="FC-RT-020",
        scenario_id="S03",
        status=CheckStatus.NEEDS_REVIEW,
        severity=Severity.MEDIUM,
        verification=Verification.RUNTIME_MOCK,
        message="no expectation",
    )
    runtime = Finding(
        id=Finding.make_id("FC-RT-012", check_ref=failed.id),
        rule_id="FC-RT-012",
        title="Tool result pairs with the issued call_id",
        kind="runtime",
        verification=Verification.RUNTIME_MOCK,
        severity=Severity.HIGH,
        confidence="high",
        message="call_id mismatch",
        location=None,
        evidence=[],
        remediation="Echo call_id.",
        check_ref=failed.id,
    )
    base = read_report(_scan_to(tmp_path))
    scenarios = [
        ScenarioResult(
            scenario_id="S04",
            name="tools",
            status=CheckStatus.FAILED,
            app_run=None,
            checks=[failed],
        ),
        ScenarioResult(
            scenario_id="S03",
            name="truncation",
            status=CheckStatus.NEEDS_REVIEW,
            app_run=None,
            checks=[review],
        ),
    ]
    findings = [*base.findings, runtime]
    rep = base.model_copy(
        update={
            "findings": findings,
            "scenarios": scenarios,
            "summary": Summary.from_report_parts(findings, scenarios),
            "metadata": base.metadata.model_copy(update={"command": "test"}),
        }
    )
    path = tmp_path / "test-report.json"
    path.write_text(report_to_json(rep), encoding="utf-8")
    result = _explain(str(path), "--llm", "fake", "--yes", "--max-findings", "5")
    assert result.exit_code == 0, result.output
    explained = read_report(tmp_path / "test-report.explained.json")
    rules = [s.rule_id for s in explained.ai_suggestions]
    assert "FC-RT-012" in rules and "FC-RT-020" in rules
    review_suggestion = next(s for s in explained.ai_suggestions if s.rule_id == "FC-RT-020")
    assert review_suggestion.title == "Truncation surfaced"  # orchestrator's catalogue
    assert _deterministic(explained) == _deterministic(rep)


def _scan_to(tmp_path: Path) -> Path:
    out = tmp_path / "scan"
    runner.invoke(app, ["scan", str(APP), "--output", str(out), "--format", "json"])
    return out / REPORT_FILENAME


# --- edge cases ------------------------------------------------------------------------------


def test_zero_findings_is_nothing_to_explain(scan_report: Path, tmp_path: Path) -> None:
    rep = read_report(scan_report)
    empty = rep.model_copy(update={"findings": [], "summary": Summary.from_report_parts()})
    path = tmp_path / "empty.json"
    path.write_text(report_to_json(empty), encoding="utf-8")
    result = _explain(str(path), "--llm", "fake")
    assert result.exit_code == 0, result.output
    assert "Nothing to explain" in result.stdout
    assert not (tmp_path / "empty.explained.json").exists()


def test_newer_schema_major_exits_2(scan_report: Path, tmp_path: Path) -> None:
    data = json.loads(scan_report.read_text())
    data["metadata"]["schema_version"] = "2.0"
    path = tmp_path / "future.json"
    path.write_text(json.dumps(data))
    result = _explain(str(path), "--llm", "fake")
    assert result.exit_code == 2 and "Unsupported report schema version" in result.stderr


def test_garbage_and_missing_reports_exit_2(tmp_path: Path) -> None:
    garbage = tmp_path / "garbage.json"
    garbage.write_text("not json")
    assert _explain(str(garbage), "--llm", "fake").exit_code == 2
    assert _explain(str(tmp_path / "missing.json"), "--llm", "fake").exit_code == 2


def test_html_format_exits_2(scan_report: Path) -> None:
    for fmt in ("html", "all"):
        result = _explain(str(scan_report), "--llm", "fake", "--format", fmt)
        assert result.exit_code == 2 and "Phase 9" in result.stderr


def test_output_never_overwrites_the_input(scan_report: Path) -> None:
    before = scan_report.read_bytes()
    result = _explain(str(scan_report), "--llm", "fake", "--output", str(scan_report))
    assert result.exit_code == 2 and "overwrite" in result.stderr
    assert scan_report.read_bytes() == before


def test_snippets_with_a_wrong_repo_note_the_omission(scan_report: Path, tmp_path: Path) -> None:
    result = _explain(
        str(scan_report),
        "--llm",
        "fake",
        "--context",
        "snippets",
        "--repo",
        str(tmp_path / "nowhere"),
    )
    assert result.exit_code == 0, result.output
    assert "snippet omitted: main.py was not found under --repo" in result.stderr


def test_snippets_with_the_right_repo_list_files_in_the_consent_panel(
    scan_report: Path, no_send: list[str]
) -> None:
    env = {"BHARATCODE_API_KEY": KEY}
    result = _explain(
        str(scan_report),
        "--llm",
        "bharatcode",
        "--context",
        "snippets",
        "--repo",
        str(APP),
        env=env,
    )
    assert result.exit_code == 2  # non-interactive, but the panel was shown first
    assert "Files:       main.py" in result.stderr and "Context:     snippets" in result.stderr


def test_help_lists_explain_as_available() -> None:
    result = runner.invoke(app, ["--help"])
    available = result.output.split("Coming in later phases")[0]
    assert "explain REPORT.json" in available
