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


@pytest.mark.parametrize("rule_id", list(load_builtin_rules()))
def test_every_rule_has_bad_and_good_fixtures(rule_id: str) -> None:
    directory = FIXTURE_ROOT / rule_id
    bad = sorted(directory.glob("bad_*.py"))
    good = sorted(directory.glob("good_*.py"))
    assert bad and good
    for path in bad:
        rel = path.relative_to(FIXTURE_ROOT).as_posix()
        assert any(item["rule_id"] == rule_id for item in MANIFEST["files"][rel]), rel
    for path in good:
        rel = path.relative_to(FIXTURE_ROOT).as_posix()
        # A good fixture may still get an ``info`` note from its own rule (placeholder keys).
        assert all(
            item["rule_id"] != rule_id or item["severity"] == "info"
            for item in MANIFEST["files"][rel]
        ), rel


def test_bad_fixtures_say_they_are_intentionally_flawed() -> None:
    for path in FIXTURE_ROOT.glob("FC-[GS]E[NC]-*/bad_*.py"):
        assert "INTENTIONALLY FLAWED FIXTURE" in path.read_text(encoding="utf-8"), path


def test_bc_rules_registered_are_001_to_008() -> None:
    ids = [rule_id for rule_id in load_builtin_rules() if rule_id.startswith("FC-BC-")]
    assert ids == [f"FC-BC-00{n}" for n in range(1, 9)]


def test_gen_and_sec_rules_registered() -> None:
    ids = [rule_id for rule_id in load_builtin_rules() if not rule_id.startswith("FC-BC-")]
    assert ids == [f"FC-GEN-00{n}" for n in range(1, 8)] + ["FC-SEC-001", "FC-SEC-002"]


def test_heuristic_rules_are_at_most_medium_and_say_not_observed(report: Report) -> None:
    for finding in report.findings:
        if finding.rule_id in ("FC-GEN-001", "FC-GEN-002", "FC-GEN-003", "FC-GEN-006"):
            assert finding.confidence.value in ("medium", "low")
            assert "observed in" in finding.message


def test_fc_bc_007_is_a_hypothesis(report: Report) -> None:
    found = [f for f in report.findings if f.rule_id == "FC-BC-007"]
    assert found and all(f.kind.value == "hypothesis" for f in found)
    assert all("[UNVERIFIED]" in f.message for f in found)


def test_findings_carry_doc_source_remediation_and_redacted_excerpt(report: Report) -> None:
    for finding in report.findings:
        if not finding.rule_id.startswith("FC-BC-"):
            continue
        assert finding.doc_url == "https://bharatcode.ai/build"
        assert finding.remediation
        source = [e for e in finding.evidence if e.type == "source"]
        assert source and source[0].excerpt
        assert finding.provider == "bharatcode"


def test_gen_and_sec_findings_carry_remediation_and_excerpt(report: Report) -> None:
    for finding in report.findings:
        if finding.rule_id.startswith("FC-BC-"):
            continue
        assert finding.remediation
        source = [e for e in finding.evidence if e.type == "source"]
        assert source and source[0].excerpt


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


# --- FC-GEN/FC-SEC edge cases (plan §27 Phase 4 item 15) --------------------------------------

BC_HEAD = (
    "from openai import OpenAI\n"
    'client = OpenAI(base_url="https://bharatcode.ai/api/model/v1", timeout=30.0)\n'
)


def _rules(tmp_path: Path, body: str, *rule_ids: str) -> list[tuple[str, int, str]]:
    (tmp_path / "a.py").write_text(BC_HEAD + body, encoding="utf-8")
    request = ScanRequest(root=tmp_path, rule_ids=list(rule_ids) or None)
    found = run_scan(request, FlightCheckConfig()).findings
    return [(f.rule_id, f.location.line, f.confidence.value) for f in found]


def test_module_level_calls_use_module_facts(tmp_path: Path) -> None:
    call = 'r = client.chat.completions.create(model="m", messages=[])\n'
    bad = call + "print(r.choices[0].message.content)\n"
    assert _rules(tmp_path, bad, "FC-GEN-001") == [("FC-GEN-001", 3, "medium")]
    good = bad + 'assert r.choices[0].finish_reason != "length"\n'
    assert _rules(tmp_path, good, "FC-GEN-001") == []
    # a check inside a function does not count for module-level code
    helper = bad + "def check(c):\n    return c.finish_reason\n"
    assert _rules(tmp_path, helper, "FC-GEN-001") == [("FC-GEN-001", 3, "medium")]


def test_call_in_try_else_is_not_protected(tmp_path: Path) -> None:
    body = (
        "def f():\n"
        "    try:\n"
        "        pass\n"
        "    except Exception:\n"
        "        return None\n"
        "    else:\n"
        '        return client.responses.create(model="m", input="x", store=False)\n'
    )
    assert _rules(tmp_path, body, "FC-GEN-004") == [("FC-GEN-004", 9, "medium")]


def test_gen_rules_are_not_downgraded_for_unresolved_clients(tmp_path: Path) -> None:
    body = 'def f(c):\n    return c.responses.create(model="m", input="x", store=False)\n'
    assert _rules(tmp_path, body, "FC-GEN-004") == [("FC-GEN-004", 4, "medium")]


def test_responses_stream_context_manager_loop(tmp_path: Path) -> None:
    body = (
        "def f():\n"
        "    try:\n"
        '        with client.responses.stream(model="m", input="x", store=False) as stream:\n'
        "            for event in stream:\n"
        "                print(event.delta)\n"
        "    except Exception:\n"
        "        return None\n"
    )
    assert _rules(tmp_path, body, "FC-GEN-003") == [("FC-GEN-003", 6, "medium")]
    handled = body.replace("print(event.delta)", 'print(event.delta, "response.completed")')
    assert _rules(tmp_path, handled, "FC-GEN-003") == []


def test_short_key_is_masked_in_every_excerpt(tmp_path: Path) -> None:
    body = 'other = OpenAI(base_url="https://bharatcode.ai/api/model/v1", api_key="k9")\n'
    (tmp_path / "a.py").write_text(BC_HEAD + body, encoding="utf-8")
    report = run_scan(ScanRequest(root=tmp_path), FlightCheckConfig())
    assert {"FC-SEC-001", "FC-GEN-005"} <= {f.rule_id for f in report.findings}
    assert '"k9"' not in report.model_dump_json()
    excerpts = [e.excerpt or "" for f in report.findings for e in f.evidence]
    keyed = [text for text in excerpts if "api_key" in text]
    assert keyed and all("[REDACTED]" in text and "k9" not in text for text in keyed)
