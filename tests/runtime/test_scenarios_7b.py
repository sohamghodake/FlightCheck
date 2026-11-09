"""Phase 7B: scenarios S03/S05/S06, [test.expect] and FC-RT-020/022/023/030.

Unit tests use hand-built RecordedExchange lists (no processes); the `slow` tests run fixture
apps against the mock on 127.0.0.1 (plan §27 Phase 7B item 14).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

from flightcheck import orchestrator
from flightcheck.core.config import ExpectConfig, TestConfig, load_config
from flightcheck.core.models import (
    AppRun,
    CheckResult,
    CheckStatus,
    Endpoint,
    FindingKind,
    RecordedExchange,
    Report,
    Severity,
    TestRequest,
)
from flightcheck.mock.faults import MockAction
from flightcheck.runtime import validators as v
from flightcheck.runtime.harness import TRUNCATION_MARK
from flightcheck.runtime.scenarios import (
    EVERY_SCENARIO_CHECK_IDS,
    S03,
    S05,
    S06,
    SCENARIOS,
    STREAM_FAIL_STEP_ID,
    TRUNCATE_STEP_ID,
    TRUNCATED_TOOL_STEP_ID,
)

PASSED, FAILED, SKIPPED = CheckStatus.PASSED, CheckStatus.FAILED, CheckStatus.SKIPPED
NEEDS_REVIEW, UNSUPPORTED = CheckStatus.NEEDS_REVIEW, CheckStatus.UNSUPPORTED
NEW_CHECKS = ("FC-RT-020", "FC-RT-022", "FC-RT-023", "FC-RT-030")
CALL_ID = "call_fc_S05_1"
REPO = Path(__file__).resolve().parents[2]
APPS = REPO / "tests" / "fixtures" / "apps"


def ex(
    seq: int,
    body: dict[str, Any] | None = None,
    *,
    endpoint: Endpoint = Endpoint.RESPONSES,
    status: int = 200,
    step: str = "default",
    sha: str | None = None,
    stream: bool = False,
    headers: dict[str, str] | None = None,
    **summary: Any,
) -> RecordedExchange:
    body = body if body is not None else {"model": "qwen-3.8-27b", "input": "hi", "store": False}
    path = "/api/model/v1/" + (
        "responses" if endpoint is Endpoint.RESPONSES else "chat/completions"
    )
    return RecordedExchange(
        seq=seq,
        at_ms=seq * 1000,
        method="POST",
        path=path,
        endpoint=endpoint,
        request_headers={"authorization": "[FAKE KEY]", **(headers or {})},
        request_json=body,
        request_sha256=sha or f"sha-{seq}",
        stream=stream,
        step_id=step,
        response_status=status,
        response_summary=summary,
    )


def app(
    exit_code: int | None = 0, *, timed_out: bool = False, stdout: str = "", stderr: str = ""
) -> AppRun:
    return AppRun(
        command=["python", "main.py"],
        exit_code=exit_code,
        timed_out=timed_out,
        duration_ms=150,
        stdout_excerpt=stdout,
        stderr_excerpt=stderr,
    )


def config(
    *, recording_complete: bool = True, aborted: bool = False, **expect: str
) -> v.CheckConfig:
    test = TestConfig(
        command=["python", "main.py"],
        timeout_s=20,
        expect=ExpectConfig(**expect) if expect else None,
    )
    return v.CheckConfig(
        test=test,
        recording_complete=recording_complete,
        missing_records=0 if recording_complete else 1,
        aborted_for_limit=aborted,
    )


def run(
    check_id: str,
    exchanges: list[RecordedExchange],
    scenario: Any,
    *,
    app_run: AppRun | None = None,
    cfg: v.CheckConfig | None = None,
) -> CheckResult:
    result = v.VALIDATORS[check_id](
        exchanges, app_run if app_run is not None else app(), scenario, cfg or config()
    )
    assert result.check_id == check_id and result.scenario_id == scenario.id
    if result.status is PASSED:
        assert result.evidence  # ADR-018: a pass always carries evidence
    return result


def truncated(seq: int = 1, **kw: Any) -> RecordedExchange:
    return ex(seq, step=TRUNCATE_STEP_ID, finish="truncated", call_ids=[], **kw)


def truncated_call(seq: int = 1, **kw: Any) -> RecordedExchange:
    return ex(
        seq, step=TRUNCATED_TOOL_STEP_ID, finish="truncated_tool_call", call_ids=[CALL_ID], **kw
    )


def tool_result_body(call_id: str) -> dict[str, Any]:
    return {
        "model": "qwen-3.8-27b",
        "store": False,
        "input": [
            {"role": "user", "content": "hi"},
            {"type": "function_call_output", "call_id": call_id, "output": "{}"},
        ],
    }


# --- catalogue ----------------------------------------------------------------------------


def test_catalogue_has_all_p0_scenarios_per_plan_18_3() -> None:
    assert list(SCENARIOS) == ["S01", "S02", "S03", "S04", "S05", "S06"]
    assert S03.check_ids == ("FC-RT-020", "FC-RT-023")
    assert S05.check_ids == ("FC-RT-022", "FC-RT-023")
    assert S06.check_ids == ("FC-RT-030",)
    assert all(s.priority == "P0" for s in SCENARIOS.values())
    assert not set(NEW_CHECKS) & set(EVERY_SCENARIO_CHECK_IDS)  # report_once leaves them alone
    assert set(NEW_CHECKS) <= set(v.VALIDATORS) and set(NEW_CHECKS) <= set(v.CHECKS)


def test_new_check_severities_match_plan_17_4() -> None:
    severities = {cid: v.CHECKS[cid].severity for cid in NEW_CHECKS}
    assert severities == {
        "FC-RT-020": Severity.MEDIUM,
        "FC-RT-022": Severity.HIGH,
        "FC-RT-023": Severity.MEDIUM,
        "FC-RT-030": Severity.MEDIUM,
    }


def test_7b_scripts_are_fresh_with_distinct_step_ids() -> None:
    expected = {
        "S03": [(TRUNCATE_STEP_ID, MockAction.TRUNCATE), ("after_truncate", MockAction.OK_TEXT)],
        "S05": [
            (TRUNCATED_TOOL_STEP_ID, MockAction.TRUNCATE_TOOL_CALL),
            ("answer", MockAction.OK_TEXT),
        ],
        "S06": [(STREAM_FAIL_STEP_ID, MockAction.STREAM_FAIL_AFTER)],
    }
    for scenario in (S03, S05, S06):
        first, second = scenario.script_factory(), scenario.script_factory()
        assert first is not second
        first.next_step()
        assert second.consumed == 0
        assert [(s.id, s.action) for s in second.steps] == expected[scenario.id]
        assert all(s.params["scenario"] == scenario.id for s in second.steps)
        assert second.default_step.action is MockAction.OK_TEXT
    assert S06.script_factory().steps[0].params["n"] == 3


# --- expectation model --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "kind", "text"),
    [
        ("nonzero_exit", v.ExpectKind.NONZERO_EXIT, ""),
        ("any", v.ExpectKind.ANY, ""),
        ("stdout_contains:truncated", v.ExpectKind.STDOUT_CONTAINS, "truncated"),
        ("stderr_contains:Answer: cut", v.ExpectKind.STDERR_CONTAINS, "Answer: cut"),
    ],
)
def test_expectation_parser_forms(raw: str, kind: v.ExpectKind, text: str) -> None:
    parsed = v.Expectation.parse(raw)
    assert (parsed.kind, parsed.text, parsed.raw) == (kind, text, raw)


@pytest.mark.parametrize(
    "raw", ["", "stdout_contains:", "stderr_contains", "zero_exit", "ANY", " nonzero_exit"]
)
def test_expectation_parser_rejects_invalid_input(raw: str) -> None:
    with pytest.raises(ValueError, match="invalid expectation"):
        v.Expectation.parse(raw)


def test_check_config_parses_expectations_once() -> None:
    assert config().expectations == v.Expectations()
    cfg = config(on_truncation="nonzero_exit", on_stream_failure="stderr_contains:failed")
    assert cfg.expectations.on_truncation == v.Expectation(
        v.ExpectKind.NONZERO_EXIT, raw="nonzero_exit"
    )
    assert cfg.expectations.on_stream_failure is not None
    assert cfg.expectations.on_stream_failure.text == "failed"
    assert cfg.expectations.on_busy_exhausted is None


@pytest.mark.parametrize(
    ("raw", "run_", "met"),
    [
        ("nonzero_exit", app(1), True),
        ("nonzero_exit", app(0), False),
        ("nonzero_exit", app(-9), True),
        ("any", app(0), True),
        ("any", app(3), True),
        ("stdout_contains:cut short", app(0, stdout="answer was cut short"), True),
        ("stdout_contains:cut short", app(0, stderr="cut short"), False),
        ("stderr_contains:cut short", app(1, stderr="error: cut short"), True),
        ("stdout_contains:cut short", app(0, stdout="x" + TRUNCATION_MARK), None),
        ("nonzero_exit", app(None, timed_out=True), None),
        ("any", app(None, timed_out=True), None),
    ],
)
def test_expectation_met(raw: str, run_: AppRun, met: bool | None) -> None:
    assert v.expectation_met(v.Expectation.parse(raw), run_) is met


# --- FC-RT-020 ----------------------------------------------------------------------------


def test_rt020_met_is_passed_with_process_evidence() -> None:
    result = run(
        "FC-RT-020",
        [truncated()],
        S03,
        app_run=app(1, stderr="Answer truncated"),
        cfg=config(on_truncation="nonzero_exit"),
    )
    assert result.status is PASSED and result.severity is Severity.MEDIUM
    assert {e.type for e in result.evidence} == {"traffic", "process"}
    assert any("Answer truncated" in e.excerpt for e in result.evidence)
    assert result.expected == "nonzero_exit" and result.observed == "exit 1"


def test_rt020_not_met_is_failed() -> None:
    result = run(
        "FC-RT-020",
        [truncated(), ex(2, step="after_truncate", finish="completed")],
        S03,
        app_run=app(0, stdout="FlightCheck partial [S03]"),
        cfg=config(on_truncation="stdout_contains:truncated"),
    )
    assert result.status is FAILED and "did not meet on_truncation" in result.message


def test_rt020_without_expectation_is_needs_review_with_exit_and_excerpt() -> None:
    result = run("FC-RT-020", [truncated()], S03, app_run=app(0, stdout="FlightCheck partial"))
    assert result.status is NEEDS_REVIEW
    assert result.observed == "exit 0" and "[test.expect] on_truncation" in (result.expected or "")
    assert any(e.type == "process" and "FlightCheck partial" in e.excerpt for e in result.evidence)


def test_rt020_timeout_never_decides() -> None:
    result = run(
        "FC-RT-020",
        [truncated()],
        S03,
        app_run=app(None, timed_out=True),
        cfg=config(on_truncation="nonzero_exit"),
    )
    assert result.status is NEEDS_REVIEW and "did not finish within 20 s" in result.message


def test_rt020_request_limit_abort_is_needs_review() -> None:
    result = run(
        "FC-RT-020",
        [truncated()],
        S03,
        app_run=app(-15),
        cfg=config(aborted=True, on_truncation="nonzero_exit"),
    )
    assert result.status is NEEDS_REVIEW and "stopped it" in result.message


def test_rt020_skipped_when_truncation_never_served_or_no_traffic() -> None:
    contract_400 = ex(1, status=400, step="contract", finish="error")
    assert run("FC-RT-020", [contract_400], S03).status is SKIPPED
    assert "never served" in (run("FC-RT-020", [contract_400], S03).reason or "")
    assert run("FC-RT-020", [], S03).status is SKIPPED


def test_rt020_incomplete_recording_turns_pass_into_needs_review() -> None:
    cfg = config(recording_complete=False, on_truncation="any")
    assert run("FC-RT-020", [truncated()], S03, cfg=cfg).status is NEEDS_REVIEW


# --- FC-RT-022 ----------------------------------------------------------------------------


def test_rt022_tool_result_for_truncated_call_fails() -> None:
    result = run(
        "FC-RT-022", [truncated_call(), ex(2, tool_result_body(CALL_ID), step="answer")], S05
    )
    assert result.status is FAILED and result.severity is Severity.HIGH
    assert [e.exchange_seq for e in result.evidence] == [1, 2]
    assert CALL_ID in result.message


def test_rt022_chat_tool_message_counts() -> None:
    body = {
        "model": "qwen-3.8-27b",
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "tool", "tool_call_id": CALL_ID, "content": "{}"},
        ],
    }
    chat = Endpoint.CHAT_COMPLETIONS
    exchanges = [truncated_call(endpoint=chat), ex(2, body, endpoint=chat, step="answer")]
    assert run("FC-RT-022", exchanges, S05).status is FAILED


def test_rt022_other_call_id_or_no_followup_passes() -> None:
    other = run(
        "FC-RT-022", [truncated_call(), ex(2, tool_result_body("call_x"), step="answer")], S05
    )
    assert other.status is PASSED and "not observable" in other.message
    alone = run("FC-RT-022", [truncated_call()], S05)
    assert alone.status is PASSED and alone.evidence[0].exchange_seq == 1


def test_rt022_no_tools_is_unsupported_and_unserved_is_skipped() -> None:
    no_tools = ex(1, step=TRUNCATED_TOOL_STEP_ID, finish="truncated", call_ids=[], no_tools=True)
    assert run("FC-RT-022", [no_tools], S05).status is UNSUPPORTED
    assert run("FC-RT-022", [ex(1, status=400, step="contract")], S05).status is SKIPPED


def test_rt022_failure_survives_incomplete_recording() -> None:
    exchanges = [truncated_call(), ex(2, tool_result_body(CALL_ID), step="answer")]
    cfg = config(recording_complete=False)
    assert run("FC-RT-022", exchanges, S05, cfg=cfg).status is FAILED
    assert run("FC-RT-022", exchanges[:1], S05, cfg=cfg).status is NEEDS_REVIEW


# --- FC-RT-023 ----------------------------------------------------------------------------


def test_rt023_identical_request_after_truncation_fails() -> None:
    exchanges = [
        truncated(sha="same"),
        ex(2, step="after_truncate", sha="same", finish="completed"),
    ]
    result = run("FC-RT-023", exchanges, S03)
    assert result.status is FAILED and result.severity is Severity.MEDIUM
    assert [e.exchange_seq for e in result.evidence] == [1, 2]


def test_rt023_different_request_passes_and_s05_counts_too() -> None:
    assert run("FC-RT-023", [truncated(sha="a"), ex(2, sha="b")], S03).status is PASSED
    assert run("FC-RT-023", [truncated()], S03).status is PASSED
    s05 = [truncated_call(sha="a"), ex(2, sha="a", step="answer")]
    assert run("FC-RT-023", s05, S05).status is FAILED


def test_rt023_incomplete_recording_and_unserved() -> None:
    cfg = config(recording_complete=False)
    assert run("FC-RT-023", [truncated()], S03, cfg=cfg).status is NEEDS_REVIEW
    assert run("FC-RT-023", [ex(1, status=503, step="busy")], S03).status is SKIPPED


# --- FC-RT-030 ----------------------------------------------------------------------------


def failed_stream(seq: int = 1) -> RecordedExchange:
    return ex(seq, step=STREAM_FAIL_STEP_ID, stream=True, finish="failed", call_ids=[], deltas=3)


def test_rt030_stream_failure_met_not_met_and_unset() -> None:
    met = run(
        "FC-RT-030",
        [failed_stream()],
        S06,
        app_run=app(1),
        cfg=config(on_stream_failure="nonzero_exit"),
    )
    assert met.status is PASSED and met.severity is Severity.MEDIUM
    assert "after 3 text delta(s)" in met.message
    not_met = run(
        "FC-RT-030",
        [failed_stream()],
        S06,
        app_run=app(0, stdout="FlightCheck"),
        cfg=config(on_stream_failure="nonzero_exit"),
    )
    assert not_met.status is FAILED and not_met.severity is Severity.MEDIUM
    unset = run("FC-RT-030", [failed_stream()], S06, app_run=app(0, stdout="FlightCheck"))
    assert unset.status is NEEDS_REVIEW and unset.observed == "exit 0"


def test_rt030_retry_is_info_with_discard_reminder() -> None:
    exchanges = [
        ex(1, status=500, step=STREAM_FAIL_STEP_ID, finish="error"),
        ex(2, headers={"x-stainless-retry-count": "1"}, finish="completed"),
    ]
    result = run("FC-RT-030", exchanges, S06)
    assert result.status is NEEDS_REVIEW and result.severity is Severity.INFO
    assert "HTTP 500" in result.message and "Discard any partial output" in result.message
    assert "x-stainless-retry-count" in result.message
    assert 2 in [e.exchange_seq for e in result.evidence]


def test_rt030_retry_never_softens_a_failure() -> None:
    exchanges = [ex(1, status=500, step=STREAM_FAIL_STEP_ID), ex(2, finish="completed")]
    result = run("FC-RT-030", exchanges, S06, cfg=config(on_stream_failure="nonzero_exit"))
    assert result.status is FAILED and result.severity is Severity.MEDIUM
    assert "Discard any partial output" in result.message


def test_rt030_skipped_when_failure_step_never_served() -> None:
    assert run("FC-RT-030", [ex(1, status=400, step="contract")], S06).status is SKIPPED


# --- reporting --------------------------------------------------------------------------


def test_report_once_leaves_scenario_checks_alone() -> None:
    exchanges = [truncated(sha="same"), ex(2, sha="same")]
    s03 = v.evaluate(S03, exchanges, app(0), config())
    again = v.evaluate(S03, exchanges, app(0), config())
    kept = v.report_once([(exchanges, s03), (exchanges, again)])
    for checks in kept:
        statuses = {c.check_id: c.status for c in checks}
        assert statuses["FC-RT-023"] is FAILED and statuses["FC-RT-020"] is NEEDS_REVIEW


def test_failed_new_checks_mirror_as_runtime_findings() -> None:
    result = run("FC-RT-022", [truncated_call(), ex(2, tool_result_body(CALL_ID))], S05)
    finding = v.to_finding(result)
    assert finding.kind is FindingKind.RUNTIME and finding.rule_id == "FC-RT-022"
    assert finding.severity is Severity.HIGH and finding.check_ref == "S05:FC-RT-022"


# --- integration (child processes against the mock on 127.0.0.1) ---------------------------


def parent_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    return env


def run_app(name: str, scenarios: list[str], **expect: str) -> Report:
    root = APPS / name
    overrides = {"test": {"expect": expect}} if expect else None
    request = TestRequest(root=root, allow_exec=True, scenarios=scenarios, include_scan=False)
    return orchestrator.run_test(request, load_config(root, overrides), environ=parent_env())


def statuses(report: Report, scenario_id: str) -> dict[str, CheckStatus]:
    result = next(s for s in report.scenarios if s.scenario_id == scenario_id)
    return {c.check_id: c.status for c in result.checks}


@pytest.mark.slow
def test_item_14_bc_bad_truncation_fails_rt022_and_rt023() -> None:
    report = run_app("bc_bad_truncation", ["S03", "S05"])
    assert statuses(report, "S05")["FC-RT-022"] is FAILED
    assert statuses(report, "S03")["FC-RT-023"] is FAILED
    runtime = {f.rule_id for f in report.findings if f.kind is FindingKind.RUNTIME}
    assert {"FC-RT-022", "FC-RT-023"} <= runtime


@pytest.mark.slow
def test_item_14_good_app_meets_on_truncation() -> None:
    report = run_app("bc_good_responses_tools", ["S03", "S05"], on_truncation="nonzero_exit")
    s03 = statuses(report, "S03")
    assert s03["FC-RT-020"] is PASSED and s03["FC-RT-023"] is PASSED
    assert statuses(report, "S05")["FC-RT-022"] is PASSED
    unset = run_app("bc_good_responses_tools", ["S03"])
    assert statuses(unset, "S03")["FC-RT-020"] is NEEDS_REVIEW


@pytest.mark.slow
def test_item_14_bc_bad_stream_rt030() -> None:
    unset = run_app("bc_bad_stream", ["S06"])
    s06 = next(s for s in unset.scenarios if s.scenario_id == "S06")
    assert statuses(unset, "S06")["FC-RT-030"] is NEEDS_REVIEW
    assert s06.app_run is not None and s06.app_run.exit_code == 0  # the SDK does not raise
    assert s06.exchanges[0].stream and s06.exchanges[0].response_summary["finish"] == "failed"
    strict = run_app("bc_bad_stream", ["S06"], on_stream_failure="nonzero_exit")
    assert statuses(strict, "S06")["FC-RT-030"] is FAILED
    assert "FC-RT-030" in {f.rule_id for f in strict.findings if f.kind is FindingKind.RUNTIME}


@pytest.mark.slow
def test_non_stream_s06_is_retried_by_the_sdk_and_reported_at_info() -> None:
    report = run_app("bc_good_responses_tools", ["S06"])
    s06 = next(s for s in report.scenarios if s.scenario_id == "S06")
    check = next(c for c in s06.checks if c.check_id == "FC-RT-030")
    assert s06.exchanges[0].response_status == 500
    assert check.status is NEEDS_REVIEW and check.severity is Severity.INFO
    assert "x-stainless-retry-count" in check.message
