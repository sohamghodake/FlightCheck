"""`flightcheck scan`: exit codes, JSON report, options and error messages."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from flightcheck.cli.app import app
from flightcheck.cli.render import FailOn, exceeds_fail_on
from flightcheck.core.errors import ConfigError
from flightcheck.core.models import Report, Severity
from flightcheck.reporting.json_report import REPORT_FILENAME, read_report, write_report

REPO_ROOT = Path(__file__).resolve().parents[2]
RULES = REPO_ROOT / "tests" / "fixtures" / "rules"
MANIFEST = json.loads((REPO_ROOT / "tests" / "fixtures" / "expected_findings.json").read_text())
runner = CliRunner()


def _scan(*args: str) -> tuple[int, str]:
    result = runner.invoke(app, ["scan", *args])
    return result.exit_code, result.output


def test_fixture_dir_json_report_matches_manifest(tmp_path: Path) -> None:
    code, out = _scan(str(RULES), "--format", "json", "--output", str(tmp_path))
    assert code == 1, out  # default --fail-on high and bad fixtures exist
    report = read_report(tmp_path / REPORT_FILENAME)
    got = sorted((f.location.path, f.location.line, f.rule_id) for f in report.findings)
    want = sorted(
        (path, item["line"], item["rule_id"])
        for path, items in MANIFEST["files"].items()
        for item in items
    )
    assert got == want
    assert report.metadata.command == "scan"
    assert report.repo is not None and report.repo.root_name == "rules"
    assert str(REPO_ROOT) not in (tmp_path / REPORT_FILENAME).read_text()


def test_fail_on_none_exits_zero(tmp_path: Path) -> None:
    code, out = _scan(str(RULES), "--fail-on", "none", "--output", str(tmp_path))
    assert code == 0, out
    assert "Exit 0 (--fail-on none)" in out


def test_bad_fixture_exits_one_with_terminal_output(tmp_path: Path) -> None:
    code, out = _scan(str(RULES / "FC-BC-001"), "--rule", "FC-BC-001", "--output", str(tmp_path))
    assert code == 1
    assert "FlightCheck 0.1.0 · scan" in out
    assert "Providers: bharatcode (high" in out
    assert "FC-BC-001" in out and "bad_store_true.py" in out and "L14" in out
    assert "static · high" in out
    assert "2 findings (2 high)" in out
    assert "Exit 1 (findings ≥ high)" in out


def test_good_fixture_exits_zero(tmp_path: Path) -> None:
    target = RULES / "FC-BC-001" / "good_store_false.py"
    code, out = _scan(str(target), "--rule", "FC-BC-001", "--output", str(tmp_path))
    assert code == 0, out
    assert "No findings" in out
    assert "1 file scanned" in out


def test_hypotheses_need_fail_on_hypotheses(tmp_path: Path) -> None:
    target = str(RULES / "FC-BC-007")
    only = ("--rule", "FC-BC-007")
    code, out = _scan(target, *only, "--fail-on", "low", "--output", str(tmp_path))
    assert code == 0
    assert "hypothesis · medium" in out and "[UNVERIFIED]" in out
    flags = ("--fail-on", "low", "--fail-on-hypotheses", "--output", str(tmp_path))
    code, _ = _scan(target, *only, *flags)
    assert code == 1


def test_missing_path_exits_two(tmp_path: Path) -> None:
    code, out = _scan(str(tmp_path / "nope"))
    assert code == 2
    assert "Path not found:" in out and "pass a directory containing your app" in out


def test_unknown_rule_exits_two_with_valid_ids(tmp_path: Path) -> None:
    code, out = _scan(str(RULES), "--rule", "FC-BC-999", "--output", str(tmp_path))
    assert code == 2
    assert "FC-BC-999" in out and "FC-BC-001" in out and "FC-BC-008" in out


@pytest.mark.parametrize("fmt", ["html", "all"])
def test_html_format_is_phase_9(tmp_path: Path, fmt: str) -> None:
    code, out = _scan(str(RULES), "--format", fmt, "--output", str(tmp_path))
    assert code == 2
    assert "HTML report arrives in Phase 9" in out


def test_empty_directory_exits_zero_with_notice(tmp_path: Path) -> None:
    code, out = _scan(str(tmp_path))
    assert code == 0, out
    assert "No Python files found" in out
    assert (tmp_path / ".flightcheck" / REPORT_FILENAME).is_file()
    assert (tmp_path / ".flightcheck" / ".gitignore").read_text() == "*\n"


def test_rule_filter_and_min_severity(tmp_path: Path) -> None:
    out_dir = tmp_path / "o"
    code, _ = _scan(str(RULES), "--rule", "fc-bc-006", "--format", "json", "--output", str(out_dir))
    report = read_report(out_dir / REPORT_FILENAME)
    assert code == 0  # FC-BC-006 findings are medium/info
    assert {f.rule_id for f in report.findings} == {"FC-BC-006"}
    _scan(str(RULES), "--min-severity", "high", "--format", "json", "--output", str(out_dir))
    report = read_report(out_dir / REPORT_FILENAME)
    assert report.findings and all(f.severity.rank >= Severity.HIGH.rank for f in report.findings)
    assert {f.severity.value for f in report.findings} == {"high", "critical"}


def test_provider_openai_compatible_disables_bc_rules(tmp_path: Path) -> None:
    code, out = _scan(
        str(RULES / "FC-BC-001"), "--provider", "openai_compatible", "--output", str(tmp_path)
    )
    report = read_report(tmp_path / REPORT_FILENAME)
    assert code == 1, out  # FC-SEC-001 (high) on good_other_provider.py still counts
    assert not [f for f in report.findings if f.rule_id.startswith("FC-BC-")]
    # provider-independent rules (FC-GEN/FC-SEC) are not disabled by the override
    assert {f.rule_id for f in report.findings} >= {"FC-GEN-004", "FC-GEN-005"}


def test_output_path_that_is_a_file_exits_two(tmp_path: Path) -> None:
    blocker = tmp_path / "file.txt"
    blocker.write_text("x")
    code, out = _scan(str(RULES / "FC-BC-001"), "--output", str(blocker))
    assert code == 2
    assert "not a directory" in out


def test_internal_error_exits_three(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: object, **kwargs: object) -> Report:
        raise RuntimeError("unexpected")

    monkeypatch.setattr("flightcheck.cli.app.run_scan", boom)
    code, out = _scan(str(tmp_path))
    assert code == 3
    assert "re-run with -v" in out


def test_exceeds_fail_on_thresholds(tmp_path: Path) -> None:
    _scan(str(RULES), "--format", "json", "--output", str(tmp_path))
    report = read_report(tmp_path / REPORT_FILENAME)
    assert exceeds_fail_on(report, FailOn.HIGH, include_hypotheses=False)
    assert exceeds_fail_on(report, FailOn.CRITICAL, include_hypotheses=False)  # FC-SEC-001
    bc_only = report.model_copy(
        update={"findings": [f for f in report.findings if f.rule_id.startswith("FC-BC-")]}
    )
    assert not exceeds_fail_on(bc_only, FailOn.CRITICAL, include_hypotheses=False)
    assert not exceeds_fail_on(report, FailOn.NONE, include_hypotheses=True)
    only_hypotheses = report.model_copy(
        update={"findings": [f for f in report.findings if f.kind.value == "hypothesis"]}
    )
    assert not exceeds_fail_on(only_hypotheses, FailOn.LOW, include_hypotheses=False)
    assert exceeds_fail_on(only_hypotheses, FailOn.LOW, include_hypotheses=True)


# --- json_report reader/writer --------------------------------------------------------------


def test_read_report_rejects_unknown_major(tmp_path: Path) -> None:
    _scan(str(RULES / "FC-BC-001"), "--format", "json", "--output", str(tmp_path))
    path = tmp_path / REPORT_FILENAME
    data = json.loads(path.read_text())
    data["metadata"]["schema_version"] = "2.0"
    path.write_text(json.dumps(data))
    with pytest.raises(ConfigError, match="Unsupported report schema version") as info:
        read_report(path)
    assert info.value.exit_code == 2


@pytest.mark.parametrize("content", ["not json", "[]", '{"metadata": {}}'])
def test_read_report_rejects_garbage(tmp_path: Path, content: str) -> None:
    path = tmp_path / "r.json"
    path.write_text(content)
    with pytest.raises(ConfigError):
        read_report(path)


def test_write_report_redacts_and_round_trips(tmp_path: Path) -> None:
    _scan(str(RULES / "FC-BC-001"), "--format", "json", "--output", str(tmp_path))
    report = read_report(tmp_path / REPORT_FILENAME)
    secret = "sk-" + "Z9y8X7w6V5u4T3s2R1q0"
    dirty = report.model_copy(
        update={"findings": [report.findings[0].model_copy(update={"message": secret})]}
    )
    path = write_report(dirty, tmp_path / "again")
    assert secret not in path.read_text()
    assert read_report(path).findings[0].rule_id == "FC-BC-001"
