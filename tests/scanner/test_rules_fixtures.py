"""Manifest-driven rule fixtures: every file under tests/fixtures/rules has exact expectations."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from flightcheck.core.config import FlightCheckConfig
from flightcheck.core.models import Report, ScanRequest
from flightcheck.orchestrator import run_scan
from flightcheck.scanner.engine import load_builtin_rules

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "tests" / "fixtures" / "expected_findings.json"
MANIFEST = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
FIXTURE_ROOT = REPO_ROOT / MANIFEST["root"]
FIELDS = ("rule_id", "line", "severity", "confidence", "kind")


@pytest.fixture(scope="module")
def report() -> Report:
    return run_scan(ScanRequest(root=FIXTURE_ROOT), FlightCheckConfig())


def _observed(report: Report, path: str) -> list[dict[str, object]]:
    out = []
    for finding in report.findings:
        if finding.location is not None and finding.location.path == path:
            data = finding.model_dump(mode="json")
            data["line"] = finding.location.line
            out.append({key: data[key] for key in FIELDS})
    return out


@pytest.mark.parametrize("path", sorted(MANIFEST["files"]))
def test_fixture_findings_match_manifest(report: Report, path: str) -> None:
    assert _observed(report, path) == MANIFEST["files"][path]


def test_manifest_lists_every_fixture_file() -> None:
    on_disk = {p.relative_to(FIXTURE_ROOT).as_posix() for p in FIXTURE_ROOT.rglob("*.py")}
    assert on_disk == set(MANIFEST["files"])


def test_no_unexpected_findings_anywhere(report: Report) -> None:
    expected = sum(len(items) for items in MANIFEST["files"].values())
    assert len(report.findings) == expected


@pytest.mark.parametrize(
    "rule_id",
    [rule_id for rule_id in load_builtin_rules() if rule_id.startswith("FC-BC-")],
)
def test_every_bc_rule_has_bad_and_good_fixtures(rule_id: str) -> None:
    directory = FIXTURE_ROOT / rule_id
    bad = sorted(directory.glob("bad_*.py"))
    good = sorted(directory.glob("good_*.py"))
    assert bad and good
    for path in bad:
        rel = path.relative_to(FIXTURE_ROOT).as_posix()
        assert any(item["rule_id"] == rule_id for item in MANIFEST["files"][rel]), rel
    for path in good:
        rel = path.relative_to(FIXTURE_ROOT).as_posix()
        assert all(item["rule_id"] != rule_id for item in MANIFEST["files"][rel]), rel


def test_bc_rules_registered_are_001_to_008() -> None:
    ids = [rule_id for rule_id in load_builtin_rules() if rule_id.startswith("FC-BC-")]
    assert ids == [f"FC-BC-00{n}" for n in range(1, 9)]


def test_fc_bc_007_is_a_hypothesis(report: Report) -> None:
    found = [f for f in report.findings if f.rule_id == "FC-BC-007"]
    assert found and all(f.kind.value == "hypothesis" for f in found)
    assert all("[UNVERIFIED]" in f.message for f in found)


def test_findings_carry_doc_source_remediation_and_redacted_excerpt(report: Report) -> None:
    for finding in report.findings:
        assert finding.doc_url == "https://bharatcode.ai/build"
        assert finding.remediation
        source = [e for e in finding.evidence if e.type == "source"]
        assert source and source[0].excerpt
        assert finding.provider == "bharatcode"


def test_excerpt_is_redacted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "sk-" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6"
    (tmp_path / "app.py").write_text(
        "from openai import OpenAI\n"
        'client = OpenAI(base_url="https://bharatcode.ai/api/model/v1")\n'
        f'client.responses.create(model="qwen-3.8-27b", input="hi", store=True, '
        f'metadata={{"k": "{secret}"}})\n',
        encoding="utf-8",
    )
    result = run_scan(ScanRequest(root=tmp_path), FlightCheckConfig())
    dumped = result.model_dump_json()
    assert result.findings
    assert secret not in dumped
