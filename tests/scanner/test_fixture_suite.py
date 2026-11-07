"""Fixture apps (plan §25.1): exact findings per app, clean apps, and runnable-program shape."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from flightcheck.core.config import FlightCheckConfig
from flightcheck.core.models import Report, ScanRequest
from flightcheck.orchestrator import run_scan

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = json.loads(
    (REPO_ROOT / "tests" / "fixtures" / "expected_findings.json").read_text(encoding="utf-8")
)
SUITE = MANIFEST["apps"]
APPS_ROOT = REPO_ROOT / SUITE["root"]
APPS = SUITE["items"]
FIELDS = ("rule_id", "line", "severity", "confidence", "kind")
P0_APPS = {
    "bc_good_responses_tools",
    "bc_good_chat_stream",
    "openai_plain",
    "bc_bad_stateful",
    "bc_bad_hosted_tools",
    "bc_bad_tool_pairing",
    "bc_bad_truncation",
    "bc_bad_stream",
    "bc_bad_no_resilience",
    "bc_bad_secrets",
    "bc_bad_budget_model",
    "bc_bad_hardcoded_base_url",
    "known_fp_helper_check",
}


def _scan(app: str) -> Report:
    return run_scan(ScanRequest(root=APPS_ROOT / app), FlightCheckConfig())


def _observed(report: Report) -> dict[str, list[dict[str, object]]]:
    out: dict[str, list[dict[str, object]]] = {}
    for finding in report.findings:
        assert finding.location is not None
        data = finding.model_dump(mode="json")
        data["line"] = finding.location.line
        out.setdefault(finding.location.path, []).append({key: data[key] for key in FIELDS})
    return out


def test_every_p0_app_is_in_the_manifest_and_on_disk() -> None:
    on_disk = {p.name for p in APPS_ROOT.iterdir() if p.is_dir()}
    assert on_disk == set(APPS) == P0_APPS


@pytest.mark.parametrize("app", sorted(APPS))
def test_app_findings_match_manifest(app: str) -> None:
    expected = {path: items for path, items in APPS[app]["files"].items() if items}
    assert _observed(_scan(app)) == expected


@pytest.mark.parametrize("app", sorted(APPS))
def test_manifest_lists_every_app_file(app: str) -> None:
    root = APPS_ROOT / app
    assert {p.relative_to(root).as_posix() for p in root.rglob("*.py")} == set(APPS[app]["files"])


@pytest.mark.parametrize("app", sorted(a for a in APPS if APPS[a]["clean"]))
def test_clean_apps_have_no_non_info_findings(app: str) -> None:
    assert [f.rule_id for f in _scan(app).findings if f.severity.value != "info"] == []


def test_clean_flags_match_plan() -> None:
    clean = {app for app, item in APPS.items() if item["clean"]}
    assert clean == {"bc_good_responses_tools", "bc_good_chat_stream", "openai_plain"}


@pytest.mark.parametrize("app", sorted(APPS))
def test_app_is_a_real_program(app: str) -> None:
    source = (APPS_ROOT / app / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert 'if __name__ == "__main__":' in source
    assert "sys.argv[1]" in source
    assert "from openai import OpenAI" in source
    assert any(isinstance(node, ast.FunctionDef) and node.name == "main" for node in tree.body)
    if app != "openai_plain":
        assert 'os.environ["BHARATCODE_API_KEY"]' in source or app == "bc_bad_secrets"
        assert 'os.environ["BHARATCODE_BASE_URL"]' in source or app == ("bc_bad_hardcoded_base_url")


@pytest.mark.parametrize("app", sorted(a for a in APPS if a.startswith("bc_bad_")))
def test_bad_apps_say_they_are_intentionally_flawed(app: str) -> None:
    assert "INTENTIONALLY FLAWED FIXTURE" in (APPS_ROOT / app / "main.py").read_text()


def test_known_fp_is_recorded_with_its_false_positive() -> None:
    items = APPS["known_fp_helper_check"]["files"]["main.py"]
    assert [item["rule_id"] for item in items] == ["FC-GEN-001"]
    assert APPS["known_fp_helper_check"]["clean"] is False
