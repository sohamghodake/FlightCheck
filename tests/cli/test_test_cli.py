"""`flightcheck test`: consent, warning panel, exit codes, output and report files (§23.2)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from flightcheck import orchestrator
from flightcheck.cli.app import app
from flightcheck.core.models import CheckStatus, FindingKind
from flightcheck.mock.server import MockServer, MockServerError
from flightcheck.reporting.json_report import REPORT_FILENAME, TRAFFIC_FILENAME, read_report
from flightcheck.runtime import harness

REPO_ROOT = Path(__file__).resolve().parents[2]
APPS = REPO_ROOT / "tests" / "fixtures" / "apps"
PAIRING = APPS / "bc_bad_tool_pairing"
GOOD = APPS / "bc_good_responses_tools"
PLANTED_KEY = "bc-planted-cli-secret-7f3a9c2e1d"
runner = CliRunner()


def _env() -> dict[str, str]:
    """`python` in the fixture commands must resolve to this venv (run_test uses os.environ)."""
    return {
        "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ.get('PATH', '')}",
        "BHARATCODE_API_KEY": PLANTED_KEY,
        "COLUMNS": "120",
    }


def _test(*args: str) -> Any:
    return runner.invoke(app, ["test", *args, "--no-color"], env=_env())


@pytest.fixture
def no_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if anything tries to start the mock or a child process."""

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("must not start a mock server or a process")

    monkeypatch.setattr(MockServer, "start", boom)
    monkeypatch.setattr(harness.subprocess, "Popen", boom)
    monkeypatch.setattr(orchestrator, "run_app", boom)


# --- nothing runs -------------------------------------------------------------------------


def test_without_allow_exec_exits_2_and_starts_nothing(tmp_path: Path, no_execution: None) -> None:
    result = _test(str(PAIRING), "--output", str(tmp_path))
    assert result.exit_code == 2, result.output
    assert "--allow-exec" in result.stderr
    assert "Execution warning" not in result.output
    assert not (tmp_path / REPORT_FILENAME).exists()


def test_allow_exec_is_never_implied_by_config(tmp_path: Path, no_execution: None) -> None:
    root = tmp_path / "app"
    root.mkdir()
    (root / "flightcheck.toml").write_text(
        '[test]\ncommand = ["python", "main.py"]\nallow_exec = true\n', encoding="utf-8"
    )
    assert _test(str(root)).exit_code == 2  # no such config key, and no consent either


def test_missing_test_section_prints_a_ready_to_paste_block(
    tmp_path: Path, no_execution: None
) -> None:
    (tmp_path / "main.py").write_text("print('hi')\n", encoding="utf-8")
    result = _test(str(tmp_path), "--allow-exec")
    assert result.exit_code == 2, result.output
    assert "No [test] section" in result.stderr
    assert '[test]\ncommand = ["python", "main.py", ' in result.stderr
    assert "Execution warning" not in result.output


@pytest.mark.parametrize(
    "args",
    [
        ["--config", "other.toml"],
        ["--timeout", "0"],
        ["--timeout", "601"],
        ["--scenario", "S99"],
    ],
)
def test_usage_errors_exit_2(args: list[str], no_execution: None) -> None:
    result = _test(str(GOOD), "--allow-exec", *args)
    assert result.exit_code == 2, result.output


def test_mock_start_failure_exits_3_after_the_panel(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(self: MockServer) -> MockServer:
        raise MockServerError("Mock server on 127.0.0.1:1 did not start within 5 s.")

    monkeypatch.setattr(MockServer, "start", fail)
    monkeypatch.setattr(harness.subprocess, "Popen", lambda *a, **k: pytest.fail("ran the app"))
    result = _test(str(GOOD), "--allow-exec", "--scenario", "S01", "--no-scan")
    assert result.exit_code == 3, result.output
    assert "Execution warning" in result.stderr
    assert "did not start" in result.stderr and "-v" in result.stderr


# --- real runs (mock on 127.0.0.1 + child process) ----------------------------------------


@pytest.mark.slow
def test_acceptance_tool_pairing_prints_failed_rt012_and_exits_1(tmp_path: Path) -> None:
    """§27 Phase 7B item 18."""
    result = _test(str(PAIRING), "--allow-exec", "--output", str(tmp_path))
    assert result.exit_code == 1, result.output
    panel = result.stderr
    assert (
        "Your code will run" in panel and "python main.py 'What is the weather in Delhi?'" in panel
    )
    assert "bc_bad_tool_pairing" in panel and "S01 Baseline" in panel and "S06 Stream" in panel
    assert "NOT run" not in panel
    out = result.stdout
    assert "FlightCheck 0.1.0 · test" in out and " Scenarios" in out
    assert "FAILED" in out and "FC-RT-012 [S04]" in out
    assert 'tool result call_id="call_1"' in out  # the traffic excerpt
    assert "FC-GEN-007" in out  # merged scan
    assert "Exit 1 (findings ≥ high)" in out
    report_text = (tmp_path / REPORT_FILENAME).read_text(encoding="utf-8")
    assert PLANTED_KEY not in result.output and PLANTED_KEY not in report_text
    report = read_report(tmp_path / REPORT_FILENAME)
    assert report.metadata.command == "test"
    assert [s.scenario_id for s in report.scenarios] == ["S01", "S02", "S03", "S04", "S05", "S06"]
    assert "S04:FC-RT-012" in {f.check_ref for f in report.findings}


@pytest.mark.slow
def test_json_format_no_scan_keep_traffic_and_timeout(tmp_path: Path) -> None:
    result = _test(
        str(PAIRING),
        "--allow-exec",
        "--scenario",
        "S04",
        "--format",
        "json",
        "--no-scan",
        "--keep-traffic",
        "--timeout",
        "15",
        "--fail-on",
        "none",
        "--output",
        str(tmp_path),
    )
    assert result.exit_code == 0, result.output  # --fail-on none
    assert result.stdout.splitlines()[0].startswith("Report written: ")
    assert result.stdout.splitlines()[1].startswith("Traffic written: ")
    assert "Timeout:   15 s" in result.stderr
    report = read_report(tmp_path / REPORT_FILENAME)
    assert report.metadata.options["timeout_s"] == 15
    assert all(f.kind is FindingKind.RUNTIME for f in report.findings)  # --no-scan
    traffic = json.loads((tmp_path / TRAFFIC_FILENAME).read_text(encoding="utf-8"))
    assert [s["scenario_id"] for s in traffic["scenarios"]] == ["S04"]
    exchanges = traffic["scenarios"][0]["exchanges"]
    assert len(exchanges) == 2 and exchanges[0]["request_headers"]["authorization"] == "[FAKE KEY]"
    assert PLANTED_KEY not in (tmp_path / TRAFFIC_FILENAME).read_text(encoding="utf-8")


@pytest.mark.slow
def test_needs_review_never_fails_the_run(tmp_path: Path) -> None:
    result = _test(
        str(GOOD), "--allow-exec", "--scenario", "S03", "--no-scan", "--fail-on", "info",
        "--output", str(tmp_path),
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert "NEEDS REVIEW" in result.stdout and "FC-RT-020 [S03]" in result.stdout
    report = read_report(tmp_path / REPORT_FILENAME)
    checks = {c.check_id: c.status for c in report.scenarios[0].checks}
    assert checks["FC-RT-020"] is CheckStatus.NEEDS_REVIEW and report.findings == []


@pytest.mark.parametrize("fmt", ["html", "all"])
def test_html_formats_write_the_html_report(tmp_path: Path, no_execution: None, fmt: str) -> None:
    """Phase 9 (ADR-032): the pre-flight refuses this app, so nothing runs, but the report
    (all checks skipped) is still written as JSON and HTML."""
    root = APPS / "bc_bad_hardcoded_base_url"
    result = _test(
        str(root), "--allow-exec", "--scenario", "S01", "--format", fmt, "--output", str(tmp_path)
    )
    assert result.exit_code in (0, 1), result.output
    html_path = tmp_path / "flightcheck-report.html"
    assert f"HTML report written: {html_path}" in result.stdout
    assert (tmp_path / REPORT_FILENAME).is_file()
    html = html_path.read_text(encoding="utf-8")
    assert 'id="scenario-S01"' in html and "st-skipped" in html
    assert PLANTED_KEY not in html
    if fmt == "html":
        assert result.stdout.splitlines()[0] == f"Report written: {tmp_path / REPORT_FILENAME}"
        assert " Scenarios" not in result.stdout
    else:
        assert " Scenarios" in result.stdout and "Exit " in result.stdout


def test_hardcoded_base_url_shows_the_panel_but_never_runs(
    tmp_path: Path, no_execution: None
) -> None:
    root = APPS / "bc_bad_hardcoded_base_url"
    result = _test(str(root), "--allow-exec", "--scenario", "S01", "--output", str(tmp_path))
    assert result.exit_code in (0, 1), result.output
    assert "Execution warning" in result.stderr and "will NOT run" in result.stderr
    assert "Your code will run" not in result.stderr
    assert "test_preflight_refused" in result.stdout and "hard-coded at main.py:15" in result.stdout
    report = read_report(tmp_path / REPORT_FILENAME)
    assert report.scenarios[0].app_run is None
    assert {c.status for c in report.scenarios[0].checks} == {CheckStatus.SKIPPED}
