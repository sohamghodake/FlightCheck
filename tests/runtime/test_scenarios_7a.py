"""Scenarios S01/S02/S04 and orchestrator.run_test: consent, pre-flight, fixtures end to end."""

from __future__ import annotations

import json
import logging
import os
import sys
import textwrap
import tomllib
from pathlib import Path
from typing import Any

import pytest

from flightcheck import orchestrator
from flightcheck.core.config import load_config
from flightcheck.core.errors import ConfigError, ConsentRequiredError
from flightcheck.core.models import CheckStatus, FindingKind, Report, TestRequest
from flightcheck.mock.faults import MockAction
from flightcheck.mock.server import MockServer, MockServerError
from flightcheck.runtime import harness
from flightcheck.runtime.scenarios import (
    EVERY_SCENARIO_CHECK_IDS,
    KNOWN_SCENARIO_IDS,
    S01,
    S02,
    S04,
    SCENARIOS,
)

REPO = Path(__file__).resolve().parents[2]
APPS = REPO / "tests" / "fixtures" / "apps"
EXPECTED = json.loads((REPO / "tests" / "fixtures" / "expected_runtime.json").read_text("utf-8"))
SCENARIO_IDS = ["S01", "S02", "S04"]


def parent_env(**extra: str) -> dict[str, str]:
    """PATH starts with the interpreter that has the openai package (fixtures run "python")."""
    env = dict(os.environ)
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    env.update(extra)
    return env


def make_app(tmp_path: Path, code: str, toml: str = "") -> Path:
    root = tmp_path / "app"
    root.mkdir()
    (root / "main.py").write_text(textwrap.dedent(code), encoding="utf-8")
    (root / "flightcheck.toml").write_text(
        textwrap.dedent(toml) or '[test]\ncommand = ["python", "main.py", "q"]\ntimeout_s = 15\n',
        encoding="utf-8",
    )
    return root


def run(root: Path, scenarios: list[str] | None = None, **env: str) -> Report:
    request = TestRequest(root=root, allow_exec=True, scenarios=scenarios)
    return orchestrator.run_test(request, load_config(root), environ=parent_env(**env))


@pytest.fixture
def no_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if anything tries to start the mock or a child process."""

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("must not start a mock server or a process")

    monkeypatch.setattr(MockServer, "start", boom)
    monkeypatch.setattr(harness.subprocess, "Popen", boom)
    monkeypatch.setattr(orchestrator, "run_app", boom)


# --- catalogue ----------------------------------------------------------------------------


def test_catalogue_matches_plan_18_3() -> None:
    assert list(SCENARIOS) == ["S01", "S02", "S04"]
    assert S02.check_ids == ("FC-RT-040", "FC-RT-041")
    assert S04.check_ids == ("FC-RT-011", "FC-RT-012", "FC-RT-013", "FC-RT-014")
    assert S01.all_check_ids == ("FC-RT-008", *EVERY_SCENARIO_CHECK_IDS)
    assert all(s.priority == "P0" for s in SCENARIOS.values())
    assert set(KNOWN_SCENARIO_IDS) >= {"S01", "S02", "S02b", "S03", "S05", "S06", "S10"}


def test_script_factories_return_fresh_scripts() -> None:
    for scenario in SCENARIOS.values():
        first, second = scenario.script_factory(), scenario.script_factory()
        assert first is not second
        first.next_step()
        assert second.consumed == 0
    s02 = S02.script_factory()
    actions = [(s.id, s.action, s.params.get("status")) for s in s02.steps]
    assert actions == [
        ("busy_1", MockAction.HTTP_ERROR, 503),
        ("busy_2", MockAction.HTTP_ERROR, 503),
        ("recover", MockAction.OK_TEXT, None),
    ]
    assert [s.action for s in S04.script_factory().steps] == [
        MockAction.TOOL_CALL,
        MockAction.OK_TEXT,
    ]
    assert all(s.params["scenario"] == "S04" for s in S04.script_factory().steps)


# --- consent, configuration and pre-flight (nothing runs) --------------------------------


def test_run_test_without_consent_raises_before_anything_starts(no_execution: None) -> None:
    root = APPS / "bc_good_responses_tools"
    with pytest.raises(ConsentRequiredError) as info:
        orchestrator.run_test(TestRequest(root=root, allow_exec=False), load_config(root))
    assert "--allow-exec" in (info.value.hint or "")


def test_consent_is_checked_before_configuration(tmp_path: Path, no_execution: None) -> None:
    with pytest.raises(ConsentRequiredError):
        orchestrator.run_test(
            TestRequest(root=tmp_path / "missing", allow_exec=False), load_config(tmp_path)
        )


def test_missing_test_section_is_a_config_error(tmp_path: Path, no_execution: None) -> None:
    with pytest.raises(ConfigError, match=r"\[test\]"):
        orchestrator.run_test(TestRequest(root=tmp_path, allow_exec=True), load_config(tmp_path))


def test_unknown_scenario_is_a_config_error(no_execution: None) -> None:
    with pytest.raises(ConfigError, match="S99"):
        run(APPS / "bc_good_responses_tools", ["S01", "S99"])


def test_cwd_outside_the_root_is_refused(tmp_path: Path, no_execution: None) -> None:
    root = make_app(tmp_path, "print(1)\n", '[test]\ncommand = ["python", "main.py"]\ncwd = ".."\n')
    with pytest.raises(Exception, match="outside"):
        run(root)


def test_phase_7b_and_p1_scenarios_become_diagnostics_not_results(no_execution: None) -> None:
    report = run(APPS / "bc_good_responses_tools", ["S03", "S05", "S06", "S08"])
    assert report.scenarios == []
    notes = [d for d in report.diagnostics if d.code == "scenario_not_implemented"]
    assert [n.message.split()[1] for n in notes] == ["S03", "S05", "S06", "S08"]
    assert "Phase 7B" in notes[0].message and "P1" in notes[3].message


def test_hardcoded_base_url_never_runs_and_skips_every_check(no_execution: None) -> None:
    report = run(APPS / "bc_bad_hardcoded_base_url", SCENARIO_IDS)
    assert [s.scenario_id for s in report.scenarios] == SCENARIO_IDS
    for scenario in report.scenarios:
        assert scenario.status is CheckStatus.SKIPPED and scenario.app_run is None
        assert scenario.exchanges == []
        for check in scenario.checks:
            assert check.status is CheckStatus.SKIPPED
            assert "hard-coded at main.py:15" in (check.reason or "")
    assert not [f for f in report.findings if f.kind is FindingKind.RUNTIME]
    assert any(d.code == "test_preflight_refused" for d in report.diagnostics)


def test_preflight_ignores_env_and_local_base_urls(tmp_path: Path) -> None:
    code = """
    import os
    from openai import OpenAI
    a = OpenAI(base_url=os.environ.get("BHARATCODE_BASE_URL", "https://bharatcode.ai/api/model/v1"))
    b = OpenAI(base_url="http://127.0.0.1:8000/v1")
    c = OpenAI(base_url="http://localhost:8000/v1")
    """
    root = make_app(tmp_path, code)
    from flightcheck.scanner.discovery import DiscoveryOptions
    from flightcheck.scanner.parser import build_index

    index = build_index(root, DiscoveryOptions())
    assert orchestrator.hardcoded_base_urls(index) == []
    (root / "main.py").write_text(
        'from openai import OpenAI\nBASE = "https://bharatcode.ai/api/model/v1"\n'
        "client = OpenAI(base_url=BASE)\n",
        encoding="utf-8",
    )
    found = orchestrator.hardcoded_base_urls(build_index(root, DiscoveryOptions()))
    assert [(loc.path, loc.line) for loc in found] == [("main.py", 3)]


def test_mock_start_failure_propagates_with_exit_3(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(self: MockServer) -> MockServer:
        raise MockServerError("Mock server on 127.0.0.1:1 did not start within 5 s.")

    monkeypatch.setattr(MockServer, "start", fail)
    with pytest.raises(MockServerError) as info:
        run(APPS / "bc_good_responses_tools", ["S01"])
    assert info.value.exit_code == 3


# --- slow: real mock + real child process -------------------------------------------------


def _statuses(report: Report) -> dict[str, Any]:
    out: dict[str, Any] = {
        s.scenario_id: {
            "status": s.status.value,
            "checks": {c.check_id: c.status.value for c in s.checks},
        }
        for s in report.scenarios
    }
    out["runtime_findings"] = sorted(
        {f.rule_id for f in report.findings if f.kind is FindingKind.RUNTIME}
    )
    return out


def test_expected_runtime_covers_every_fixture_app() -> None:
    apps = sorted(p.name for p in APPS.iterdir() if (p / "main.py").is_file())
    assert sorted(EXPECTED["apps"]) == apps
    assert EXPECTED["scenarios"] == SCENARIO_IDS


@pytest.mark.slow
@pytest.mark.parametrize("app_name", sorted(EXPECTED["apps"]))
def test_fixture_app_runtime_expectations(app_name: str) -> None:
    report = run(APPS / app_name, SCENARIO_IDS)
    assert _statuses(report) == EXPECTED["apps"][app_name]
    for scenario in report.scenarios:
        for check in scenario.checks:
            if check.status is CheckStatus.PASSED:
                assert check.evidence
    runtime = [f for f in report.findings if f.kind is FindingKind.RUNTIME]
    failed = {c.id for s in report.scenarios for c in s.checks if c.status is CheckStatus.FAILED}
    assert {f.check_ref for f in runtime} == failed
    Report.model_validate_json(report.model_dump_json())  # report schema round trip


@pytest.mark.slow
def test_item_14_acceptance_details() -> None:
    """Plan §27 Phase 7A item 14, spelled out (the JSON above freezes the full picture)."""
    good = _statuses(run(APPS / "bc_good_responses_tools", SCENARIO_IDS))
    assert good["runtime_findings"] == []
    assert good["S02"]["checks"] == {"FC-RT-040": "passed", "FC-RT-041": "needs_review"}
    pairing = _statuses(run(APPS / "bc_bad_tool_pairing", ["S04"]))
    assert pairing["S04"]["checks"]["FC-RT-012"] == "failed"
    assert pairing["S04"]["checks"]["FC-RT-013"] == "failed"
    resilience = _statuses(run(APPS / "bc_bad_no_resilience", ["S02"]))
    assert resilience["S02"]["checks"]["FC-RT-040"] == "failed"
    stateful = run(APPS / "bc_bad_stateful", ["S01"])
    s01 = stateful.scenarios[0]
    checks = {c.check_id: c.status for c in s01.checks}
    assert checks["FC-RT-001"] is CheckStatus.FAILED and checks["FC-RT-002"] is CheckStatus.FAILED
    assert s01.app_run is not None and s01.app_run.exit_code != 0  # both recorded
    assert s01.exchanges[0].response_status == 400
    assert checks["FC-RT-008"] is CheckStatus.NEEDS_REVIEW


@pytest.mark.slow
def test_include_scan_adds_static_findings_like_run_scan() -> None:
    root = APPS / "bc_bad_stateful"
    with_scan = run(root, ["S01"])
    static = [f for f in with_scan.findings if f.kind is not FindingKind.RUNTIME]
    from flightcheck.core.models import ScanRequest

    scanned = orchestrator.run_scan(ScanRequest(root=root), load_config(root))
    assert [f.id for f in static] == [f.id for f in scanned.findings]
    request = TestRequest(root=root, allow_exec=True, scenarios=["S01"], include_scan=False)
    without = orchestrator.run_test(request, load_config(root), environ=parent_env())
    assert all(f.kind is FindingKind.RUNTIME for f in without.findings)
    assert with_scan.metadata.command == "test"
    assert with_scan.metadata.mock_fidelity == "documentation_model"
    assert with_scan.summary.checks_by_verification == {
        "runtime_mock": len(with_scan.scenarios[0].checks)
    }


@pytest.mark.slow
def test_command_not_found_is_an_error_with_a_hint(tmp_path: Path) -> None:
    root = make_app(tmp_path, "print(1)\n", '[test]\ncommand = ["flightcheck-no-such-cmd"]\n')
    report = run(root, ["S01", "S02"])
    for scenario in report.scenarios:
        assert scenario.status is CheckStatus.ERROR and scenario.app_run is None
        assert all(c.status is CheckStatus.ERROR for c in scenario.checks)
        assert all("check [test].command" in c.message for c in scenario.checks)


@pytest.mark.slow
def test_app_that_makes_no_requests_skips_traffic_checks(tmp_path: Path) -> None:
    root = make_app(tmp_path, "import sys\nprint(sys.stdin.read() == '')\n")
    report = run(root, ["S01"])
    scenario = report.scenarios[0]
    assert scenario.app_run is not None and scenario.app_run.stdout_excerpt.strip() == "True"
    checks = {c.check_id: c for c in scenario.checks}
    assert checks["FC-RT-008"].status is CheckStatus.NEEDS_REVIEW
    for cid in EVERY_SCENARIO_CHECK_IDS:
        assert checks[cid].status is CheckStatus.SKIPPED
        assert "no traffic observed" in (checks[cid].reason or "")
    assert scenario.status is CheckStatus.NEEDS_REVIEW


@pytest.mark.slow
def test_app_exceeding_max_requests_is_stopped_and_failed(tmp_path: Path) -> None:
    code = """
    import os, time
    from openai import OpenAI
    client = OpenAI(base_url=os.environ["BHARATCODE_BASE_URL"],
                    api_key=os.environ["BHARATCODE_API_KEY"], max_retries=0)
    while True:
        try:
            client.responses.create(model="qwen-3.8-27b", input="again", store=False)
        except Exception:
            pass
        time.sleep(0.01)
    """
    toml = '[test]\ncommand = ["python", "main.py"]\ntimeout_s = 20\nmax_requests = 3\n'
    report = run(make_app(tmp_path, code, toml), ["S01"])
    scenario = report.scenarios[0]
    rt060 = next(c for c in scenario.checks if c.check_id == "FC-RT-060")
    assert rt060.status is CheckStatus.FAILED and "FlightCheck stopped the app" in rt060.message
    assert scenario.app_run is not None and scenario.app_run.timed_out is False
    assert scenario.app_run.duration_ms < 15_000
    assert any(f.rule_id == "FC-RT-060" for f in report.findings)


@pytest.mark.slow
def test_secrets_never_reach_the_child_report_or_logs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    code = """
    import os, sys
    from openai import OpenAI
    print("ENV", sorted(os.environ))
    print("AWS", os.environ.get("AWS_SECRET_ACCESS_KEY"), os.environ.get("GITHUB_TOKEN"))
    print("planted bc_live_PLANTEDBYCHILD00001", file=sys.stderr)
    client = OpenAI(base_url=os.environ["BHARATCODE_BASE_URL"],
                    api_key=os.environ["BHARATCODE_API_KEY"])
    print(client.responses.create(model="qwen-3.8-27b", input="hi", store=False).output_text)
    """
    planted = {
        "BHARATCODE_API_KEY": "bc_live_PARENTREALKEY0000001",
        "OPENAI_API_KEY": "sk-parentrealkey00000000001",
        "AWS_SECRET_ACCESS_KEY": "awsParentSecret0000000000001",
        "GITHUB_TOKEN": "ghp_parenttoken000000000000000001",
    }
    caplog.set_level(logging.DEBUG, logger="flightcheck")
    report = run(make_app(tmp_path, code), ["S01"], **planted)
    scenario = report.scenarios[0]
    assert scenario.app_run is not None and scenario.app_run.exit_code == 0
    assert "AWS None None" in scenario.app_run.stdout_excerpt
    assert "bc_live_PLANTEDBYCHILD00001" not in scenario.app_run.stderr_excerpt
    rt050 = next(c for c in scenario.checks if c.check_id == "FC-RT-050")
    assert rt050.status is CheckStatus.PASSED  # the child used the fake key
    dumped = report.model_dump_json() + caplog.text
    for value in planted.values():
        assert value not in dumped
    assert "bc_live_PLANTEDBYCHILD00001" not in dumped
    assert str(Path.home()) not in scenario.app_run.stderr_excerpt


# --- fixture configuration ----------------------------------------------------------------


@pytest.mark.parametrize(
    "app_dir", sorted(p for p in APPS.iterdir() if p.is_dir()), ids=lambda p: p.name
)
def test_fixture_toml_files_load_without_project_tables(app_dir: Path) -> None:
    data = tomllib.loads((app_dir / "flightcheck.toml").read_text("utf-8"))
    assert set(data) == {"test"}  # no [project]: it would change the static scan
    config = load_config(app_dir)
    assert config.provider is None
    assert config.test is not None and config.test.command[0] == "python"
    assert config.test.timeout_s <= 20
