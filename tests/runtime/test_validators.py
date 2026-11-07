"""Validators: pure checks over hand-built RecordedExchange lists (no processes, no sockets)."""

from __future__ import annotations

from typing import Any

import pytest

from flightcheck.core.config import TestConfig
from flightcheck.core.models import (
    AppRun,
    CheckResult,
    CheckStatus,
    Confidence,
    Endpoint,
    FindingKind,
    RecordedExchange,
    Severity,
    Verification,
)
from flightcheck.runtime import validators as v
from flightcheck.runtime.scenarios import (
    EVERY_SCENARIO_CHECK_IDS,
    S01,
    S02,
    S04,
    SCENARIOS,
)

PASSED, FAILED, SKIPPED = CheckStatus.PASSED, CheckStatus.FAILED, CheckStatus.SKIPPED
NEEDS_REVIEW, UNSUPPORTED = CheckStatus.NEEDS_REVIEW, CheckStatus.UNSUPPORTED
CALL_ID = "call_fc_S04_1"
TOOLS = [{"type": "function", "name": "get_weather", "parameters": {"type": "object"}}]


def ex(
    seq: int,
    body: dict[str, Any] | None = None,
    *,
    endpoint: Endpoint | None = Endpoint.RESPONSES,
    status: int = 200,
    step: str = "baseline",
    at_ms: int | None = None,
    auth: str | None = "fake_key",
    headers: dict[str, str] | None = None,
    **summary: Any,
) -> RecordedExchange:
    if body is None:
        body = {"model": "qwen-3.8-27b", "input": "hi", "store": False}
    path = {
        Endpoint.RESPONSES: "/api/model/v1/responses",
        Endpoint.CHAT_COMPLETIONS: "/api/model/v1/chat/completions",
        Endpoint.MODELS: "/api/model/v1/models",
        None: "/api/model/v1/nope",
    }[endpoint]
    if auth is not None:
        summary.setdefault("auth", auth)
    return RecordedExchange(
        seq=seq,
        at_ms=seq * 1000 if at_ms is None else at_ms,
        method="GET" if endpoint is Endpoint.MODELS else "POST",
        path=path,
        endpoint=endpoint,
        request_headers={"authorization": "[FAKE KEY]", **(headers or {})},
        request_json=body,
        request_sha256=f"sha-{seq}-{sorted(body.items()) if body else ''}",
        stream=False,
        step_id=step,
        response_status=status,
        response_summary=summary,
    )


def app(exit_code: int | None = 0, *, timed_out: bool = False, stderr: str = "") -> AppRun:
    return AppRun(
        command=["python", "main.py"],
        exit_code=exit_code,
        timed_out=timed_out,
        duration_ms=120,
        stdout_excerpt="",
        stderr_excerpt=stderr,
    )


CONFIG = v.CheckConfig(test=TestConfig(command=["python", "main.py"], max_requests=5))


def run(check_id: str, exchanges: list[RecordedExchange], scenario=S01, **kw: Any) -> CheckResult:
    config = kw.pop("config", CONFIG)
    result = v.VALIDATORS[check_id](exchanges, kw.pop("app_run", app()), scenario, config)
    assert result.verification is Verification.RUNTIME_MOCK
    assert result.check_id == check_id and result.id == f"{scenario.id}:{check_id}"
    if result.status is PASSED:
        assert result.evidence
    return result


def body(**fields: Any) -> dict[str, Any]:
    return {"model": "qwen-3.8-27b", "input": "hi", "store": False, **fields}


# --- registry ------------------------------------------------------------------------------


def test_every_scenario_check_has_a_validator_and_metadata() -> None:
    ids = {cid for s in SCENARIOS.values() for cid in s.all_check_ids}
    assert ids == set(v.VALIDATORS) == set(v.CHECKS)


@pytest.mark.parametrize(
    ("check_id", "severity"),
    [
        ("FC-RT-001", Severity.HIGH),
        ("FC-RT-002", Severity.HIGH),
        ("FC-RT-003", Severity.HIGH),
        ("FC-RT-004", Severity.HIGH),
        ("FC-RT-005", Severity.HIGH),
        ("FC-RT-006", Severity.MEDIUM),
        ("FC-RT-007", Severity.MEDIUM),
        ("FC-RT-012", Severity.HIGH),
        ("FC-RT-013", Severity.HIGH),
        ("FC-RT-014", Severity.HIGH),
        ("FC-RT-040", Severity.MEDIUM),
        ("FC-RT-041", Severity.LOW),
        ("FC-RT-050", Severity.CRITICAL),
        ("FC-RT-060", Severity.HIGH),
    ],
)
def test_severities_follow_plan_17_4(check_id: str, severity: Severity) -> None:
    assert v.CHECKS[check_id].severity is severity


# --- zero traffic and incomplete recordings -------------------------------------------------


@pytest.mark.parametrize("check_id", sorted(set(v.VALIDATORS) - {"FC-RT-008"}))
def test_zero_traffic_skips_every_traffic_check(check_id: str) -> None:
    scenario = next(s for s in SCENARIOS.values() if check_id in s.all_check_ids)
    result = run(check_id, [], scenario)
    assert result.status is SKIPPED
    assert result.reason == v.NO_TRAFFIC_REASON


def test_zero_traffic_but_incomplete_recording_is_needs_review() -> None:
    config = v.CheckConfig(test=CONFIG.test, recording_complete=False, missing_records=2)
    result = run("FC-RT-001", [], config=config)
    assert result.status is NEEDS_REVIEW and "2 request(s) not recorded" in result.message


def test_incomplete_recording_turns_a_pass_into_needs_review_but_keeps_failures() -> None:
    config = v.CheckConfig(test=CONFIG.test, recording_complete=False, missing_records=1)
    assert run("FC-RT-001", [ex(1)], config=config).status is NEEDS_REVIEW
    assert run("FC-RT-001", [ex(1, body(store=True))], config=config).status is FAILED


# --- FC-RT-001…007 ------------------------------------------------------------------------


def test_rt001_store_false_true_missing_and_odd_values() -> None:
    assert run("FC-RT-001", [ex(1), ex(2)]).status is PASSED
    failed = run("FC-RT-001", [ex(1), ex(2, body(store=True))])
    assert failed.status is FAILED and "seq 2" in failed.message
    assert [e.exchange_seq for e in failed.evidence] == [2]
    missing = {"model": "qwen-3.8-27b", "input": "hi"}
    review = run("FC-RT-001", [ex(1, missing)])
    assert review.status is NEEDS_REVIEW and "[UNVERIFIED]" in review.message
    assert run("FC-RT-001", [ex(1, body(store=None))]).status is NEEDS_REVIEW
    assert run("FC-RT-001", [ex(1, body(store="false"))]).status is FAILED
    assert run("FC-RT-001", [ex(1, body(store=0))]).status is FAILED


def test_contract_checks_apply_to_responses_only() -> None:
    chat = ex(1, {"model": "qwen-3.8-27b", "messages": []}, endpoint=Endpoint.CHAT_COMPLETIONS)
    for cid in ("FC-RT-001", "FC-RT-002", "FC-RT-003", "FC-RT-004", "FC-RT-005"):
        result = run(cid, [chat])
        assert result.status is SKIPPED and "/responses" in (result.reason or "")


@pytest.mark.parametrize(
    ("check_id", "bad", "good"),
    [
        ("FC-RT-002", {"previous_response_id": "resp_1"}, {"previous_response_id": None}),
        ("FC-RT-003", {"tools": [{"type": "web_search"}]}, {"tools": TOOLS}),
        ("FC-RT-004", {"background": True}, {"background": False}),
        (
            "FC-RT-005",
            {"input": [{"role": "user", "content": [{"type": "input_file", "file_id": "f"}]}]},
            {"input": [{"role": "user", "content": [{"type": "input_text", "text": "x"}]}]},
        ),
    ],
)
def test_documented_400_checks(check_id: str, bad: dict, good: dict) -> None:
    failed = run(check_id, [ex(1, body(**good)), ex(2, body(**bad))])
    assert failed.status is FAILED and [e.exchange_seq for e in failed.evidence] == [2]
    assert "HTTP 400" in failed.message
    assert run(check_id, [ex(1, body(**good))]).status is PASSED


def test_rt006_budget_cap_on_both_endpoints() -> None:
    assert run("FC-RT-006", [ex(1, body(max_output_tokens=32_000))]).status is PASSED
    assert run("FC-RT-006", [ex(1)]).status is PASSED  # unset: provider default
    assert run("FC-RT-006", [ex(1, body(max_output_tokens=32_001))]).status is FAILED
    chat = {"model": "qwen-3.8-27b", "messages": [], "max_tokens": 64_000}
    result = run("FC-RT-006", [ex(1, chat, endpoint=Endpoint.CHAT_COMPLETIONS)])
    assert result.status is FAILED and "max_tokens=64000" in (result.observed or "")
    assert run("FC-RT-006", [ex(1, body(max_output_tokens="lots"))]).status is NEEDS_REVIEW
    assert run("FC-RT-006", [ex(1, body(max_output_tokens=True))]).status is NEEDS_REVIEW


def test_rt007_model_catalogue() -> None:
    assert run("FC-RT-007", [ex(1)]).status is PASSED
    assert run("FC-RT-007", [ex(1, body(model="gpt-4o-mini"))]).status is FAILED
    no_model = {"input": "hi", "store": False}
    assert run("FC-RT-007", [ex(1, no_model)]).status is FAILED
    pro = run("FC-RT-007", [ex(1, body(model="deepseek-v4.1-flash"))])
    assert pro.status is NEEDS_REVIEW and pro.severity is Severity.INFO and "Pro" in pro.message


def test_model_listing_alone_is_not_model_traffic() -> None:
    models = ex(1, None, endpoint=Endpoint.MODELS, step="models")
    models = models.model_copy(update={"request_json": None})
    assert run("FC-RT-007", [models]).status is SKIPPED
    assert run("FC-RT-050", [models]).status is PASSED


# --- FC-RT-008 ----------------------------------------------------------------------------


def test_rt008_baseline_exit() -> None:
    assert run("FC-RT-008", [ex(1)]).status is PASSED
    nonzero = run("FC-RT-008", [ex(1)], app_run=app(1, stderr="Traceback ..."))
    assert nonzero.status is NEEDS_REVIEW and nonzero.observed == "exit 1"
    assert run("FC-RT-008", [ex(1)], app_run=app(None, timed_out=True)).status is NEEDS_REVIEW
    assert run("FC-RT-008", [], app_run=app(0)).status is NEEDS_REVIEW  # exit 0, no traffic
    assert run("FC-RT-008", [], app_run=None).status is SKIPPED


# --- FC-RT-011…014 (S04) ------------------------------------------------------------------


def tool_ex(seq: int = 1, **summary: Any) -> RecordedExchange:
    summary.setdefault("finish", "tool_call")
    summary.setdefault("call_ids", [CALL_ID])
    return ex(seq, body(input="weather in Delhi?", tools=TOOLS), step="tool_call", **summary)


def follow(
    seq: int = 2, *, call_id: str = CALL_ID, output: Any = '{"temp_c": 31}', **kw: Any
) -> RecordedExchange:
    items = kw.pop(
        "items",
        [
            {"role": "user", "content": "weather in Delhi?"},
            {"type": "function_call", "call_id": CALL_ID, "name": "get_weather", "arguments": "{}"},
            {"type": "function_call_output", "call_id": call_id, "output": output},
        ],
    )
    return ex(seq, body(input=items, tools=kw.pop("tools", TOOLS)), step="answer")


def test_s04_good_round_trip_passes_every_tool_check() -> None:
    traffic = [tool_ex(), follow()]
    for cid in S04.check_ids:
        assert run(cid, traffic, S04).status is PASSED, cid


def test_s04_app_without_tools_is_unsupported() -> None:
    traffic = [ex(1, step="tool_call", no_tools=True, finish="completed", call_ids=[])]
    for cid in S04.check_ids:
        result = run(cid, traffic, S04)
        assert result.status is UNSUPPORTED and "app defines no tools" in (result.reason or "")


def test_s04_tool_step_never_served_is_skipped() -> None:
    rejected = ex(1, body(store=True), status=400, step="contract")
    for cid in S04.check_ids:
        result = run(cid, [rejected], S04)
        assert result.status is SKIPPED and "contract" in (result.reason or "")


def test_rt011_no_follow_up_is_needs_review_and_later_checks_skip() -> None:
    result = run("FC-RT-011", [tool_ex()], S04)
    assert result.status is NEEDS_REVIEW and CALL_ID in result.message
    for cid in ("FC-RT-012", "FC-RT-013", "FC-RT-014"):
        assert run(cid, [tool_ex()], S04).status is SKIPPED


def test_rt012_detects_a_mismatched_call_id() -> None:
    result = run("FC-RT-012", [tool_ex(), follow(call_id="call_1")], S04)
    assert result.status is FAILED
    assert '"call_1"' in result.message and CALL_ID in result.message


def test_rt012_detects_a_missing_tool_result() -> None:
    items = [{"role": "user", "content": "weather in Delhi?"}]
    result = run("FC-RT-012", [tool_ex(), follow(items=items)], S04)
    assert result.status is FAILED and "function_call_output" in result.message
    assert run("FC-RT-013", [tool_ex(), follow(items=items)], S04).status is SKIPPED


def test_rt013_detects_dict_output() -> None:
    result = run("FC-RT-013", [tool_ex(), follow(output={"temp_c": 31})], S04)
    assert result.status is FAILED and "dict" in result.message
    assert run("FC-RT-013", [tool_ex(), follow(output=None)], S04).status is FAILED


def test_rt014_detects_missing_original_input() -> None:
    items = [
        {"type": "function_call", "call_id": CALL_ID, "name": "get_weather", "arguments": "{}"},
        {"type": "function_call_output", "call_id": CALL_ID, "output": "{}"},
    ]
    result = run("FC-RT-014", [tool_ex(), follow(items=items)], S04)
    assert result.status is FAILED and "original user input" in result.message


def test_rt014_detects_missing_function_call_item() -> None:
    items = [
        {"role": "user", "content": "weather in Delhi?"},
        {"type": "function_call_output", "call_id": CALL_ID, "output": "{}"},
    ]
    result = run("FC-RT-014", [tool_ex(), follow(items=items)], S04)
    assert result.status is FAILED and "function_call item" in result.message


def test_rt014_tools_not_resent_is_needs_review() -> None:
    result = run("FC-RT-014", [tool_ex(), follow(tools=[])], S04)
    assert result.status is NEEDS_REVIEW and "resend tool definitions" in result.message


def chat(seq: int, messages: list, *, step: str, **summary: Any) -> RecordedExchange:
    payload = {"model": "qwen-3.8-27b", "messages": messages, "tools": TOOLS}
    return ex(seq, payload, endpoint=Endpoint.CHAT_COMPLETIONS, step=step, **summary)


def test_chat_round_trip_pairs_tool_call_id_and_is_labelled_unverified() -> None:
    user = {"role": "user", "content": "weather?"}
    call = {"id": CALL_ID, "type": "function", "function": {"name": "get_weather"}}
    assistant = {"role": "assistant", "content": None, "tool_calls": [call]}
    tool = {"role": "tool", "tool_call_id": CALL_ID, "content": '{"t": 31}'}
    first = chat(1, [user], step="tool_call", finish="tool_call", call_ids=[CALL_ID])
    good = [first, chat(2, [user, assistant, tool], step="answer")]
    assert run("FC-RT-012", good, S04).status is PASSED
    passed = run("FC-RT-014", good, S04)
    assert passed.status is PASSED and "[UNVERIFIED]" in passed.message
    no_assistant = [first, chat(2, [user, tool], step="answer")]
    failed = run("FC-RT-014", no_assistant, S04)
    assert failed.status is FAILED and "[UNVERIFIED]" in failed.message
    assert v.to_finding(failed).confidence is Confidence.MEDIUM
    parts = {**tool, "content": [{"type": "text", "text": "31"}]}
    listed = run("FC-RT-013", [first, chat(2, [user, assistant, parts], step="answer")], S04)
    assert listed.status is NEEDS_REVIEW


# --- FC-RT-040/041 (S02) ------------------------------------------------------------------


def busy(
    seq: int, at_ms: int, retry: int | None = None, step: str | None = None
) -> RecordedExchange:
    headers = {} if retry is None else {"x-stainless-retry-count": str(retry)}
    return ex(
        seq,
        status=503 if step is None else 200,
        step=step or f"busy_{seq}",
        at_ms=at_ms,
        headers=headers,
    )


def test_rt040_counts_attempts() -> None:
    one = run("FC-RT-040", [busy(1, 10)], S02, app_run=app(1))
    assert (
        one.status is FAILED and "Only one request" in one.message and "exit code 1" in one.message
    )
    three = [busy(1, 10, 0), busy(2, 450, 1), busy(3, 1300, 2, step="recover")]
    passed = run("FC-RT-040", three, S02)
    assert passed.status is PASSED and "3 attempts" in passed.message
    assert "OpenAI SDK" in passed.message  # attributed to the SDK's default retries


def test_rt040_without_sdk_header_is_not_attributed_to_the_sdk() -> None:
    passed = run("FC-RT-040", [busy(1, 10), busy(2, 3000, step="recover")], S02)
    assert passed.status is PASSED and "SDK" not in passed.message


def test_rt040_041_skip_when_busy_step_never_served() -> None:
    rejected = ex(1, body(store=True), status=400, step="contract")
    for cid in ("FC-RT-040", "FC-RT-041"):
        assert run(cid, [rejected], S02).status is SKIPPED


def test_rt041_timing() -> None:
    assert run("FC-RT-041", [busy(1, 10)], S02).status is SKIPPED
    fast = run("FC-RT-041", [busy(1, 10), busy(2, 110), busy(3, 220, step="recover")], S02)
    assert fast.status is FAILED and "100 ms" in fast.message
    slow = run("FC-RT-041", [busy(1, 10), busy(2, 2010), busy(3, 5010, step="recover")], S02)
    assert slow.status is PASSED and "2000 ms" in slow.message


@pytest.mark.parametrize("gaps", [(440, 830), (510, 900)])
def test_rt041_sdk_backoff_is_needs_review_whatever_the_gap(gaps: tuple[int, int]) -> None:
    traffic = [busy(1, 0, 0), busy(2, gaps[0], 1), busy(3, gaps[0] + gaps[1], 2, step="recover")]
    result = run("FC-RT-041", traffic, S02)
    assert result.status is NEEDS_REVIEW
    assert "375–500 ms" in result.message and "few seconds" in result.message


# --- FC-RT-050/060 ------------------------------------------------------------------------


def test_rt050_uses_the_auth_flag_only() -> None:
    assert run("FC-RT-050", [ex(1), ex(2)]).status is PASSED
    other = run("FC-RT-050", [ex(1), ex(2, auth="other_key")])
    assert other.status is FAILED and other.severity is Severity.CRITICAL
    assert "not stored" in other.message and "Bearer" not in other.model_dump_json()
    missing = run("FC-RT-050", [ex(1, auth="missing", status=401, step="auth")])
    assert missing.status is NEEDS_REVIEW and "BHARATCODE_API_KEY" in missing.message
    assert run("FC-RT-050", [ex(1, auth="unchecked")]).status is SKIPPED
    assert run("FC-RT-050", [ex(1, auth=None, step="request_limit")]).status is SKIPPED


def test_rt060_request_limit() -> None:
    assert run("FC-RT-060", [ex(i) for i in range(1, 6)]).status is PASSED
    over = [*(ex(i) for i in range(1, 6)), ex(6, auth=None, status=429, step="request_limit")]
    failed = run("FC-RT-060", over)
    assert failed.status is FAILED and "max_requests (5)" in failed.message
    aborted = v.CheckConfig(test=CONFIG.test, aborted_for_limit=True)
    stopped = run("FC-RT-060", over, config=aborted)
    assert "FlightCheck stopped the app" in stopped.message


# --- evaluate, report_once, to_finding ----------------------------------------------------


def test_evaluate_runs_scenario_checks_then_every_scenario_checks_sorted_by_seq() -> None:
    results = v.evaluate(S02, [busy(2, 600, step="recover"), busy(1, 10)], app(), CONFIG)
    assert [r.check_id for r in results] == ["FC-RT-040", "FC-RT-041", *EVERY_SCENARIO_CHECK_IDS]
    assert results[0].status is PASSED  # seq order, not append order


def test_passed_always_carries_evidence_across_validators() -> None:
    samples = [
        [ex(1)],
        [tool_ex(), follow()],
        [busy(1, 0), busy(2, 2000, step="recover")],
    ]
    for traffic in samples:
        for scenario in SCENARIOS.values():
            for result in v.evaluate(scenario, traffic, app(), CONFIG):
                if result.status is PASSED:
                    assert result.evidence, result.id


def test_report_once_reports_contract_checks_once_and_keeps_new_offenders() -> None:
    s01 = [ex(1, body(store=True))]
    s04 = [ex(1, body(store=True)), ex(2, body(store=True, input="other"))]
    runs = [
        (s01, v.evaluate(S01, s01, app(1), CONFIG)),
        (s01, v.evaluate(S02, s01, app(1), CONFIG)),
        (s04, v.evaluate(S04, s04, app(1), CONFIG)),
    ]
    kept = v.report_once(runs)
    ids = [[c.check_id for c in checks] for checks in kept]
    assert ids[0] == list(S01.all_check_ids)  # first scenario: everything
    assert ids[1] == ["FC-RT-040", "FC-RT-041"]  # same request again: nothing repeated
    assert "FC-RT-001" in ids[2]  # a new offending request body
    assert "FC-RT-002" not in ids[2]  # passed already reported in S01


def test_report_once_keeps_a_later_observation_after_an_early_skip() -> None:
    runs = [
        ([], v.evaluate(S01, [], app(), CONFIG)),
        ([ex(1)], v.evaluate(S02, [ex(1)], app(), CONFIG)),
    ]
    first, second = v.report_once(runs)
    assert next(c for c in first if c.check_id == "FC-RT-001").status is SKIPPED
    assert next(c for c in second if c.check_id == "FC-RT-001").status is PASSED


def test_report_once_keeps_request_limit_breaches_per_scenario() -> None:
    over = [*(ex(i) for i in range(1, 6)), ex(6, auth=None, status=429, step="request_limit")]
    runs = [
        (over, v.evaluate(S01, over, app(), CONFIG)),
        (over, v.evaluate(S02, over, app(), CONFIG)),
    ]
    first, second = v.report_once(runs)
    for checks in (first, second):
        assert next(c for c in checks if c.check_id == "FC-RT-060").status is FAILED


def test_to_finding_mirrors_a_failed_check() -> None:
    failed = run("FC-RT-012", [tool_ex(), follow(call_id="call_1")], S04)
    finding = v.to_finding(failed)
    assert finding.kind is FindingKind.RUNTIME
    assert finding.verification is Verification.RUNTIME_MOCK
    assert finding.check_ref == failed.id == "S04:FC-RT-012"
    assert finding.rule_id == "FC-RT-012" and finding.severity is Severity.HIGH
    assert finding.confidence is Confidence.HIGH and finding.message.startswith("[S04]")
    assert finding.id == v.to_finding(failed).id  # stable


def test_helpers() -> None:
    assert v.user_texts("q") == ["q"]
    assert v.user_texts([{"role": "user", "content": [{"type": "input_text", "text": "a"}]}]) == [
        "a"
    ]
    assert v.gaps_ms([busy(1, 5), busy(2, 25), busy(3, 70)]) == [20, 45]
    assert v.sdk_retry_count(busy(1, 0, 2)) == 2 and v.sdk_retry_count(busy(1, 0)) is None
