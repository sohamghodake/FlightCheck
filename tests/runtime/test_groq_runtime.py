"""Groq runs of `flightcheck test` (phase G4, ADR-038): env, provider, gating, Groq checks.

Fast tests use synthetic exchanges; the ``slow`` ones run fixture apps on the real mock.
"""

from __future__ import annotations

import os
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from flightcheck import orchestrator
from flightcheck.core.config import (
    GROQ_TEST_API_KEY_ENV,
    GROQ_TEST_BASE_URL_ENV,
    GROQ_TEST_SCENARIOS,
    TestConfig,
    load_config,
)
from flightcheck.core.models import (
    AppRun,
    CheckResult,
    CheckStatus,
    Confidence,
    Endpoint,
    RecordedExchange,
    Severity,
    TestRequest,
)
from flightcheck.runtime import validators as v
from flightcheck.runtime.harness import ALWAYS_SCRUBBED, build_child_env
from flightcheck.runtime.scenarios import (
    EVERY_SCENARIO_CHECK_IDS,
    GROQ_EVERY_SCENARIO_CHECK_IDS,
    RATE_LIMIT_HEADERS,
    RATE_LIMIT_STEP_ID,
    S01,
    S02,
    S04,
    S05,
    S06,
    S11,
    SCENARIOS,
)

PASSED, FAILED, SKIPPED = CheckStatus.PASSED, CheckStatus.FAILED, CheckStatus.SKIPPED
NEEDS_REVIEW, UNSUPPORTED = CheckStatus.NEEDS_REVIEW, CheckStatus.UNSUPPORTED
REPO = Path(__file__).resolve().parents[2]
APPS = REPO / "tests" / "fixtures" / "apps"
PLANTED_GROQ_KEY = "gsk_" + "PlantedRealGroqKey0000000001"
PLANTED_GROQ_URL = "https://api.groq.com"
GROQ_CONFIG = v.CheckConfig(test=TestConfig(command=["python", "main.py"]), provider="groq")
BC_CONFIG = v.CheckConfig(test=TestConfig(command=["python", "main.py"]))
MODEL = "openai/gpt-oss-20b"


def gx(
    seq: int,
    body: dict[str, Any] | None = None,
    *,
    endpoint: Endpoint = Endpoint.CHAT_COMPLETIONS,
    status: int = 200,
    step: str = "baseline",
    at_ms: int | None = None,
    received_ms: int | None = None,
    headers: dict[str, str] | None = None,
    **summary: Any,
) -> RecordedExchange:
    """A Groq-route exchange (path /openai/v1/..., provider and arrival time recorded)."""
    if body is None:
        body = {"model": MODEL, "messages": [{"role": "user", "content": "hi"}]}
    path = (
        "/openai/v1/responses"
        if endpoint is Endpoint.RESPONSES
        else ("/openai/v1/chat/completions")
    )
    answered = seq * 1000 if at_ms is None else at_ms
    summary.setdefault("auth", "fake_key")
    summary["provider"] = "groq"
    summary["received_ms"] = answered if received_ms is None else received_ms
    return RecordedExchange(
        seq=seq,
        at_ms=answered,
        method="POST",
        path=path,
        endpoint=endpoint,
        request_headers={"authorization": "[FAKE KEY]", **(headers or {})},
        request_json=body,
        request_sha256=f"sha-{seq}-{sorted(body.items())}",
        stream=False,
        step_id=step,
        response_status=status,
        response_summary=summary,
    )


def rx(seq: int, **fields: Any) -> RecordedExchange:
    """A Groq /responses exchange."""
    return gx(seq, {"model": MODEL, "input": "hi", **fields}, endpoint=Endpoint.RESPONSES)


def app(exit_code: int | None = 0) -> AppRun:
    return AppRun(
        command=["python", "main.py"],
        exit_code=exit_code,
        timed_out=False,
        duration_ms=50,
        stdout_excerpt="",
        stderr_excerpt="",
    )


def run(
    check_id: str,
    exchanges: list[RecordedExchange],
    scenario=S01,  # type: ignore[no-untyped-def]
    config: v.CheckConfig = GROQ_CONFIG,
) -> CheckResult:
    result = v.VALIDATORS[check_id](exchanges, app(), scenario, config)
    if result.status is PASSED:
        assert result.evidence
    if config.provider == "groq":
        assert "BharatCode" not in result.message  # Groq runs never cite BharatCode
    return result


# --- harness environment (FC-RT-050, contract K3/K4, §4.9) -----------------------------------


def _parent(**extra: str) -> dict[str, str]:
    return {
        "PATH": str(Path(sys.executable).parent) + os.pathsep + "/usr/bin",
        "HOME": "/tmp",
        "GROQ_API_KEY": PLANTED_GROQ_KEY,
        "GROQ_BASE_URL": PLANTED_GROQ_URL,
        **extra,
    }


def test_groq_variables_are_never_inherited_on_a_bharatcode_run() -> None:
    env = build_child_env(_parent(), TestConfig(command=["x"]), "http://127.0.0.1:9/api", "k")
    assert "GROQ_API_KEY" not in env and "GROQ_BASE_URL" not in env
    assert PLANTED_GROQ_KEY not in env.values() and PLANTED_GROQ_URL not in env.values()
    assert set(ALWAYS_SCRUBBED) == {"GROQ_API_KEY", "GROQ_BASE_URL"}


def test_groq_variables_stay_scrubbed_even_if_the_allowlist_grows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defence in depth: the explicit scrub holds even if a future allowlist named them."""
    from flightcheck.runtime import harness

    monkeypatch.setattr(harness, "ENV_ALLOWLIST", (*harness.ENV_ALLOWLIST, *ALWAYS_SCRUBBED))
    env = build_child_env(_parent(), TestConfig(command=["x"]), "http://m/api", "k")
    assert "GROQ_API_KEY" not in env and "GROQ_BASE_URL" not in env


def test_groq_run_injects_fake_key_mock_root_and_openai_prefix() -> None:
    test = TestConfig(command=["x"]).for_provider("groq")
    env = build_child_env(
        _parent(),
        test,
        "http://127.0.0.1:9/openai/v1",
        "fc_fake_key_g",
        mock_root="http://127.0.0.1:9",
        guard_port=1,
    )
    assert env["GROQ_API_KEY"] == env["OPENAI_API_KEY"] == "fc_fake_key_g"
    assert env["GROQ_BASE_URL"] == "http://127.0.0.1:9"  # the groq SDK adds /openai/v1 (K4)
    assert env["OPENAI_BASE_URL"] == "http://127.0.0.1:9/openai/v1"
    assert PLANTED_GROQ_KEY not in env.values()
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:1"  # the egress guard still comes last


def test_custom_base_url_names_get_the_openai_prefix_and_groq_base_url_the_root() -> None:
    test = TestConfig(command=["x"], base_url_env=["GROQ_OPENAI_BASE_URL", "GROQ_BASE_URL"])
    env = build_child_env(_parent(), test, "http://m/openai/v1", "k", mock_root="http://m")
    assert env["GROQ_OPENAI_BASE_URL"] == "http://m/openai/v1"
    assert env["GROQ_BASE_URL"] == "http://m"
    without_root = build_child_env(_parent(), test, "http://m/api/model/v1", "k")
    assert without_root["GROQ_BASE_URL"] == "http://m/api/model/v1"  # no root given: unchanged


def test_test_config_groq_defaults_fill_only_unset_fields() -> None:
    plain = TestConfig(command=["x"])
    groq = plain.for_provider("groq")
    assert groq.base_url_env == list(GROQ_TEST_BASE_URL_ENV) == ["GROQ_BASE_URL", "OPENAI_BASE_URL"]
    assert groq.api_key_env == list(GROQ_TEST_API_KEY_ENV) == ["GROQ_API_KEY", "OPENAI_API_KEY"]
    assert groq.scenarios == list(GROQ_TEST_SCENARIOS) and groq.scenarios[-1] == "S11"
    assert plain.for_provider("bharatcode") is plain
    assert groq.for_provider("groq") == groq  # idempotent
    explicit = TestConfig(command=["x"], base_url_env=["MY_URL"], scenarios=["S01"])
    resolved = explicit.for_provider("groq")
    assert resolved.base_url_env == ["MY_URL"] and resolved.scenarios == ["S01"]
    assert resolved.api_key_env == ["GROQ_API_KEY", "OPENAI_API_KEY"]


# --- which runs are Groq runs (ADR-038) -------------------------------------------------------


def _app(tmp_path: Path, code: str, toml: str = "") -> Path:
    root = tmp_path / "app"
    root.mkdir(parents=True)
    (root / "main.py").write_text(textwrap.dedent(code), encoding="utf-8")
    (root / "flightcheck.toml").write_text(
        toml + '\n[test]\ncommand = ["python", "main.py"]\n', encoding="utf-8"
    )
    return root


def _plan(root: Path, scenarios: list[str] | None = None) -> orchestrator.TestPlan:
    request = TestRequest(root=root, allow_exec=True, scenarios=scenarios)
    return orchestrator.plan_test(request, load_config(root))


GROQ_CLIENT = """
import os
from openai import OpenAI
client = OpenAI(base_url=os.environ["GROQ_OPENAI_BASE_URL"], api_key=os.environ["GROQ_API_KEY"])
client.chat.completions.create(model="openai/gpt-oss-20b", messages=[])
"""
MODEL_ONLY = """
import os
from openai import OpenAI
client = OpenAI(base_url=os.environ["OPENAI_BASE_URL"], api_key=os.environ["OPENAI_API_KEY"])
client.chat.completions.create(model="openai/gpt-oss-20b", messages=[])
"""


def test_a_groq_app_is_planned_as_a_groq_run(tmp_path: Path) -> None:
    plan = _plan(_app(tmp_path, GROQ_CLIENT))
    assert plan.provider == "groq"
    assert [s.id for s in plan.scenarios] == list(GROQ_TEST_SCENARIOS)
    assert plan.test.api_key_env == ["GROQ_API_KEY", "OPENAI_API_KEY"]


def test_a_groq_model_id_alone_does_not_make_a_groq_run(tmp_path: Path) -> None:
    plan = _plan(_app(tmp_path, MODEL_ONLY))
    assert plan.provider == "bharatcode"
    assert [s.id for s in plan.scenarios] == ["S01", "S02", "S03", "S04", "S05", "S06"]


def test_declared_groq_provider_makes_a_groq_run(tmp_path: Path) -> None:
    plan = _plan(_app(tmp_path, MODEL_ONLY, '[project]\nprovider = "groq"\n'))
    assert plan.provider == "groq"


def test_declared_groq_wins_over_a_detection_conflict(tmp_path: Path) -> None:
    """A BharatCode signal plus [project] provider = "groq" makes detection "unknown"
    (conflict); the developer's declaration still decides the run."""
    code = MODEL_ONLY.replace("OPENAI_BASE_URL", "BHARATCODE_BASE_URL")
    root = _app(tmp_path, code, '[project]\nprovider = "groq"\n')
    plan = _plan(root)
    index = plan.index
    from flightcheck.scanner.detect import detect

    assert detect(index, declared_provider="groq").repo.provider == "unknown"  # type: ignore[union-attr]
    assert plan.provider == "groq"


def test_existing_fixture_apps_stay_bharatcode_runs() -> None:
    for root in sorted(APPS.iterdir()):
        expected = "groq" if root.name.startswith("groq_") else "bharatcode"
        assert _plan(root).provider == expected, root.name


def test_s11_on_a_bharatcode_run_is_a_diagnostic_not_a_result(tmp_path: Path) -> None:
    plan = _plan(_app(tmp_path, MODEL_ONLY), ["S01", "S11"])
    assert [s.id for s in plan.scenarios] == ["S01"]
    (note,) = plan.notes
    assert note.code == "scenario_not_applicable" and "S11" in note.message
    with pytest.raises(orchestrator.ConfigError):
        _plan(_app(tmp_path / "x", MODEL_ONLY), ["S12"])


def test_ready_test_section_leaves_the_provider_defaults_alone(tmp_path: Path) -> None:
    import tomllib

    block = orchestrator.ready_test_section(tmp_path)
    parsed = tomllib.loads(block)["test"]
    assert set(parsed) == {"command", "cwd", "timeout_s"}  # no explicit variable lists
    resolved = TestConfig(**parsed).for_provider("groq")
    assert resolved.base_url_env == list(GROQ_TEST_BASE_URL_ENV)
    assert "GROQ_BASE_URL" in block and "BHARATCODE_BASE_URL" in block


# --- gating table (ADR-038): every check × provider -------------------------------------------


def _evaluated(provider: str) -> set[str]:
    return {
        cid
        for s in SCENARIOS.values()
        if provider in s.providers
        for cid in s.check_ids_for(provider)  # type: ignore[arg-type]
    }


@pytest.mark.parametrize("provider", ["bharatcode", "groq"])
@pytest.mark.parametrize("check_id", sorted(v.CHECKS))
def test_gating_table_cell(check_id: str, provider: str) -> None:
    assert (check_id in _evaluated(provider)) == (provider in v.CHECKS[check_id].providers)


def test_gating_table_rows() -> None:
    groq_only = {cid for cid, m in v.CHECKS.items() if m.providers == {"groq"}}
    bc_only = {cid for cid, m in v.CHECKS.items() if m.providers == {"bharatcode"}}
    assert groq_only == {"FC-RT-009", "FC-RT-015", "FC-RT-044"}
    assert bc_only == {"FC-RT-001", "FC-RT-003", "FC-RT-004", "FC-RT-005"}
    assert EVERY_SCENARIO_CHECK_IDS == (
        "FC-RT-001",
        "FC-RT-002",
        "FC-RT-003",
        "FC-RT-004",
        "FC-RT-005",
        "FC-RT-006",
        "FC-RT-007",
        "FC-RT-050",
        "FC-RT-060",
    )
    assert GROQ_EVERY_SCENARIO_CHECK_IDS == (
        "FC-RT-002",
        "FC-RT-006",
        "FC-RT-007",
        "FC-RT-009",
        "FC-RT-015",
        "FC-RT-050",
        "FC-RT-060",
    )
    assert S11.providers == {"groq"} and S11.check_ids == ("FC-RT-044",)
    assert all(s.providers == {"bharatcode", "groq"} for s in SCENARIOS.values() if s is not S11)
    assert S01.all_check_ids == S01.check_ids_for("bharatcode")


def test_evaluate_skip_all_and_error_all_follow_the_provider() -> None:
    groq = [r.check_id for r in v.evaluate(S01, [], app(), GROQ_CONFIG)]
    assert groq == ["FC-RT-008", *GROQ_EVERY_SCENARIO_CHECK_IDS]
    bc = [r.check_id for r in v.evaluate(S01, [], app(), BC_CONFIG)]
    assert bc == ["FC-RT-008", *EVERY_SCENARIO_CHECK_IDS]
    assert [r.check_id for r in v.skip_all(S11, "x", "groq")] == [
        "FC-RT-044",
        *GROQ_EVERY_SCENARIO_CHECK_IDS,
    ]
    assert [r.check_id for r in v.error_all(S02, "x", "groq")][:2] == ["FC-RT-040", "FC-RT-041"]


def test_report_once_covers_the_groq_every_scenario_checks() -> None:
    bad = rx(1, store=True)
    first = v.evaluate(S01, [bad], app(), GROQ_CONFIG)
    again = v.evaluate(S02, [rx(1, store=True)], app(), GROQ_CONFIG)
    kept = v.report_once([([bad], first), ([rx(1, store=True)], again)])
    assert "FC-RT-009" in {c.check_id for c in kept[0]}
    assert "FC-RT-009" not in {c.check_id for c in kept[1]}  # same offender, reported once


# --- FC-RT-009: store is not true (R5) --------------------------------------------------------


def test_rt009_store_values() -> None:
    assert run("FC-RT-009", [rx(1)]).status is PASSED  # omitted: Groq's default is false
    assert run("FC-RT-009", [rx(1, store=False), rx(2, store=None)]).status is PASSED
    result = run("FC-RT-009", [rx(1, store=False), rx(2, store=True)])
    assert result.status is FAILED and "seq 2" in result.message
    assert "R5" in result.message and "[UNVERIFIED]" in result.message
    assert run("FC-RT-009", [rx(1, store="false")]).status is FAILED  # only false or null
    chat_only = run("FC-RT-009", [gx(1)])
    assert chat_only.status is SKIPPED


def test_rt001_and_rt009_differ_on_an_omitted_store() -> None:
    """The reason for a new ID (ADR-012): FC-RT-001 wants store=false sent."""
    bc = v.VALIDATORS["FC-RT-001"]([rx(1)], app(), S01, BC_CONFIG)
    assert bc.status is NEEDS_REVIEW
    assert run("FC-RT-009", [rx(1)]).status is PASSED


# --- FC-RT-015: only tools the model supports (R9, T8) -----------------------------------------


FUNCTION = {"type": "function", "function": {"name": "f", "parameters": {"type": "object"}}}


@pytest.mark.parametrize(
    ("model", "tools", "status"),
    [
        (MODEL, [FUNCTION], PASSED),
        ("qwen/qwen3.8-27b", [FUNCTION, {"type": "mcp", "server_url": "x"}], PASSED),
        ("openai/gpt-oss-120b", [{"type": "browser_search"}], PASSED),
        (MODEL, [{"type": "code_interpreter", "container": {"type": "auto"}}], PASSED),
        ("qwen/qwen3.8-27b", [{"type": "browser_search"}], FAILED),
        ("llama-3.3-70b-versatile", [{"type": "code_interpreter"}], FAILED),
        (MODEL, [{"type": "web_search_preview"}], FAILED),  # not a Groq tool type
        ("some/new-model", [{"type": "browser_search"}], NEEDS_REVIEW),
        (MODEL, [], PASSED),
    ],
)
def test_rt015(model: str, tools: list, status: CheckStatus) -> None:
    chat = gx(1, {"model": model, "messages": [], "tools": tools})
    responses = gx(2, {"model": model, "input": "x", "tools": tools}, endpoint=Endpoint.RESPONSES)
    for exchanges in ([chat], [responses]):  # T8: built-in tools on Chat Completions too
        result = run("FC-RT-015", exchanges)
        assert result.status is status
        if status is FAILED:
            assert "R9" in result.message and "[UNVERIFIED]" in result.message


# --- FC-RT-006 / FC-RT-007 on Groq (§1.8, M1, M2) ----------------------------------------------


def test_rt006_uses_each_models_documented_maximum() -> None:
    def budget(model: str, value: Any, key: str = "max_completion_tokens") -> RecordedExchange:
        return gx(1, {"model": model, "messages": [], key: value})

    assert run("FC-RT-006", [budget(MODEL, 65_536)]).status is PASSED  # BC's 32 000 not used
    over = run("FC-RT-006", [budget("llama-3.3-70b-versatile", 32_769)])
    assert over.status is FAILED and "allows 32768" in over.message
    assert "[UNVERIFIED]" in over.message
    assert run("FC-RT-006", [budget("qwen/qwen3.8-27b", 16_385, "max_tokens")]).status is FAILED
    assert run("FC-RT-006", [budget(MODEL, "lots")]).status is NEEDS_REVIEW
    assert run("FC-RT-006", [budget("some/new-model", 10)]).status is NEEDS_REVIEW
    resp = gx(
        1, {"model": MODEL, "input": "x", "max_output_tokens": 70_000}, endpoint=Endpoint.RESPONSES
    )
    assert run("FC-RT-006", [resp]).status is FAILED
    assert run("FC-RT-006", [gx(1)]).status is PASSED  # no budget


def test_rt007_against_groqs_catalogue() -> None:
    def model(value: Any) -> RecordedExchange:
        return gx(1, {"model": value, "messages": []})

    production = run("FC-RT-007", [model(MODEL)])
    assert production.status is PASSED and "Preview" not in production.message
    preview = run("FC-RT-007", [model("qwen/qwen3.8-27b")])
    assert preview.status is PASSED and "M1" in preview.message
    gone = run("FC-RT-007", [model("qwen/qwen3-32b")])
    assert gone.status is FAILED and gone.severity is Severity.HIGH and "M2" in gone.message
    unknown = run("FC-RT-007", [model("gpt-4o")])
    assert unknown.status is FAILED and unknown.severity is Severity.MEDIUM
    assert run("FC-RT-007", [model(None)]).status is FAILED
    enterprise = run("FC-RT-007", [model("llama-3.1-8b-instant")])
    assert enterprise.status is NEEDS_REVIEW and enterprise.severity is Severity.INFO


def test_rt002_on_groq_cites_r4_and_labels_the_status_unverified() -> None:
    result = run("FC-RT-002", [rx(1, previous_response_id="resp_1")])
    assert result.status is FAILED
    assert "R4" in result.message and "[UNVERIFIED]" in result.message
    assert run("FC-RT-002", [rx(1, previous_response_id=None)]).status is PASSED


# --- FC-RT-044: 429 retry waits for retry-after (S11; E5, K6) ---------------------------------


def limited(seq: int = 1, at_ms: int = 1000) -> RecordedExchange:
    return gx(seq, status=429, step=RATE_LIMIT_STEP_ID, at_ms=at_ms, retry_after="2")


@pytest.mark.parametrize(
    ("arrived", "status"), [(3000, PASSED), (3001, PASSED), (2999, FAILED), (1003, FAILED)]
)
def test_rt044_gap_from_the_429_answer_to_the_next_arrival(
    arrived: int, status: CheckStatus
) -> None:
    # The next request is answered late (at_ms 9000) but arrived at `arrived`: the arrival
    # time decides, so a slow answer can never make a short wait look long enough.
    nxt = gx(2, step="after_rate_limit", at_ms=9000, received_ms=arrived)
    result = run("FC-RT-044", [limited(), nxt], S11)
    assert result.status is status
    assert f"{arrived - 1000} ms" in result.message and "E5" in result.message


def test_rt044_sdk_retry_is_named() -> None:
    nxt = gx(2, at_ms=3100, received_ms=3050, headers={"x-stainless-retry-count": "1"})
    result = run("FC-RT-044", [limited(), nxt], S11)
    assert result.status is PASSED and "K6" in result.message


def test_rt044_no_retry_missing_arrival_and_not_served() -> None:
    nothing = run("FC-RT-044", [limited()], S11)
    assert nothing.status is NEEDS_REVIEW and "E9" in nothing.message
    nxt = gx(2, at_ms=5000)
    del nxt.response_summary["received_ms"]
    assert run("FC-RT-044", [limited(), nxt], S11).status is NEEDS_REVIEW
    contract = gx(1, status=400, step="contract")
    assert run("FC-RT-044", [contract], S11).status is SKIPPED
    assert run("FC-RT-044", [], S11).status is SKIPPED


def test_s11_script_matches_contract_4_7() -> None:
    script = S11.script_factory()
    first, second = script.steps
    assert first.id == RATE_LIMIT_STEP_ID and first.params["status"] == 429
    assert first.params["headers"]["retry-after"] == "2"
    assert first.params["headers"] == RATE_LIMIT_HEADERS
    assert {h for h in RATE_LIMIT_HEADERS if h.startswith("x-ratelimit-")} == {
        f"x-ratelimit-{k}-{u}"
        for k in ("limit", "remaining", "reset")
        for u in ("requests", "tokens")
    }
    assert second.action.value == "ok_text"


# --- text updates on Groq runs ----------------------------------------------------------------


def tool_ex(seq: int = 1) -> RecordedExchange:
    body = {"model": MODEL, "messages": [{"role": "user", "content": "hi"}], "tools": [FUNCTION]}
    return gx(seq, body, step="tool_call", finish="tool_call", call_ids=["call_1"], tool="f")


def follow(seq: int, content: Any, call_id: str = "call_1") -> RecordedExchange:
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "tool_calls": [{"id": "call_1", "type": "function"}]},
        {"role": "tool", "tool_call_id": call_id, "content": content},
    ]
    return gx(seq, {"model": MODEL, "messages": messages, "tools": [FUNCTION]}, step="answer")


def test_groq_chat_tool_round_is_verified_not_unverified() -> None:
    exchanges = [tool_ex(), follow(2, '{"ok": true}')]
    for cid in ("FC-RT-011", "FC-RT-012", "FC-RT-013", "FC-RT-014"):
        result = run(cid, exchanges, S04)
        assert result.status is PASSED, cid
        assert "[UNVERIFIED]" not in result.message  # Chat round trip: T4/T5 verified
    mismatch = run("FC-RT-012", [tool_ex(), follow(2, "x", call_id="call_9")], S04)
    assert mismatch.status is FAILED and "T5" in mismatch.message


def test_groq_tool_output_types() -> None:
    dict_content = run("FC-RT-013", [tool_ex(), follow(2, {"ok": True})], S04)
    assert dict_content.status is FAILED and "T5" in dict_content.message
    parts = run("FC-RT-013", [tool_ex(), follow(2, [{"type": "text", "text": "x"}])], S04)
    assert parts.status is NEEDS_REVIEW and "[UNVERIFIED]" in parts.message


def test_groq_responses_continuation_is_labelled_t10() -> None:
    tool = gx(
        1,
        {"model": MODEL, "input": "hi", "tools": [FUNCTION]},
        endpoint=Endpoint.RESPONSES,
        step="tool_call",
        finish="tool_call",
        call_ids=["call_1"],
    )
    items = [{"type": "function_call_output", "call_id": "call_1", "output": {"ok": 1}}]
    bad = gx(2, {"model": MODEL, "input": items}, endpoint=Endpoint.RESPONSES, step="answer")
    output = run("FC-RT-013", [tool, bad], S04)
    assert output.status is NEEDS_REVIEW and "T10" in output.message
    replay = run("FC-RT-014", [tool, bad], S04)
    assert replay.status is FAILED and "R3" in replay.message and "T10" in replay.message
    assert v.to_finding(replay, "groq").confidence is Confidence.MEDIUM


def busy(seq: int, status: int, at_ms: int, retry: str | None = None) -> RecordedExchange:
    headers = {"x-stainless-retry-count": retry} if retry else None
    step = f"busy_{seq}" if status == 503 else "recover"
    return gx(seq, status=status, step=step, at_ms=at_ms, headers=headers)


def test_groq_busy_texts_drop_model_busy() -> None:
    once = run("FC-RT-040", [busy(1, 503, 1000)], S02)
    assert once.status is FAILED and "E3" in once.message and "busy" not in once.message
    sdk = [busy(1, 503, 1000), busy(2, 503, 1500, "1"), busy(3, 200, 2500, "2")]
    retried = run("FC-RT-040", sdk, S02)
    assert retried.status is PASSED and "groq SDKs" in retried.message
    backoff = run("FC-RT-041", sdk, S02)
    assert backoff.status is NEEDS_REVIEW and "E9" in backoff.message
    fast = run("FC-RT-041", [busy(1, 503, 1000), busy(2, 200, 1010)], S02)
    assert fast.status is FAILED and "E3" in fast.message


def test_bharatcode_texts_are_unchanged() -> None:
    bc_once = v.VALIDATORS["FC-RT-040"]([busy(1, 503, 1000)], app(), S02, BC_CONFIG)
    assert bc_once.message.endswith("the documented busy 503 was not retried.")
    fast = v.VALIDATORS["FC-RT-041"](
        [busy(1, 503, 1000), busy(2, 200, 1010)], app(), S02, BC_CONFIG
    )
    assert fast.message.endswith("wait before retrying a busy 503.")
    assert v.meta_for("FC-RT-040", "bharatcode") == (
        "Busy 503 is retried",
        "Retry a 503 (model busy) after a pause, a bounded number of times.",
        v.DOC_URL,
    )


def test_groq_stream_failure_text_names_x_groq_error() -> None:
    fault = gx(1, step="stream_fail", finish="x_groq_error", deltas=3)
    fault = fault.model_copy(update={"stream": True})
    result = run("FC-RT-030", [fault], S06)
    assert result.status is NEEDS_REVIEW
    assert "x_groq.error" in result.message and "ST3" in result.message
    assert "[UNVERIFIED]" in result.message


def test_groq_truncated_tool_call_mentions_failed_generation() -> None:
    fault = gx(1, step="truncated_tool_call", finish="truncated_tool_call", call_ids=["c1"])
    result = run("FC-RT-022", [fault], S05)
    assert (
        result.status is PASSED and "failed_generation" in result.message and "T6" in result.message
    )


def test_groq_meta_texts() -> None:
    title, remediation, doc = v.meta_for("FC-RT-040", "groq")
    assert title == "503 is retried" and "model busy" not in remediation and "E3" in remediation
    assert doc == "https://console.groq.com/docs/errors"
    _, remediation, doc = v.meta_for("FC-RT-023", "groq")
    assert remediation.startswith("FlightCheck recommendation") and doc is None
    assert "contract" not in remediation and "Groq" not in remediation  # must not cite Groq
    _, remediation, _ = v.meta_for("FC-RT-022", "groq")
    assert "T6" in remediation and "failed_generation" in remediation
    for cid in v.CHECKS:
        title, remediation, _ = v.meta_for(cid, "groq")
        assert "BharatCode" not in remediation and "keepalive" not in remediation.lower(), cid


def test_to_finding_uses_the_run_provider() -> None:
    failed = run("FC-RT-009", [rx(1, store=True)])
    finding = v.to_finding(failed, "groq")
    assert finding.provider == "groq" and finding.rule_id == "FC-RT-009"
    assert finding.doc_url == "https://console.groq.com/docs/responses-api"
    assert finding.remediation == v.CHECKS["FC-RT-009"].remediation
    bc = v.VALIDATORS["FC-RT-007"]([gx(1, {"model": "x", "messages": []})], app(), S01, BC_CONFIG)
    assert v.to_finding(bc).provider == "bharatcode" and v.to_finding(bc).doc_url == v.DOC_URL


# --- slow: Groq fixture apps on the real mock ---------------------------------------------------


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    env["GROQ_API_KEY"] = PLANTED_GROQ_KEY
    env["GROQ_BASE_URL"] = PLANTED_GROQ_URL
    return env


def _run_app(name: str, scenarios: list[str], **expect: str):  # type: ignore[no-untyped-def]
    root = APPS / name
    overrides = {"test": {"expect": expect}} if expect else None
    request = TestRequest(root=root, allow_exec=True, scenarios=scenarios, include_scan=False)
    return orchestrator.run_test(request, load_config(root, overrides), environ=_env())


@pytest.mark.slow
def test_stream_failure_expectation_decides_rt030_on_groq() -> None:
    def rt030(report):  # type: ignore[no-untyped-def]
        return next(c for c in report.scenarios[0].checks if c.check_id == "FC-RT-030")

    bad = rt030(_run_app("groq_bad_stream", ["S06"], on_stream_failure="nonzero_exit"))
    assert bad.status is FAILED and "x_groq.error" in bad.message
    good = rt030(_run_app("groq_good_chat_stream", ["S06"], on_stream_failure="nonzero_exit"))
    assert good.status is PASSED


@pytest.mark.slow
def test_planted_groq_key_never_reaches_the_child_or_the_report() -> None:
    report = _run_app("groq_good_responses_http", ["S01"])
    checks = {c.check_id: c.status for c in report.scenarios[0].checks}
    assert checks["FC-RT-050"] is PASSED  # the app sent FlightCheck's fake key
    assert report.metadata.options["mock_provider"] == "groq"
    text = report.model_dump_json()
    assert PLANTED_GROQ_KEY not in text and "PlantedRealGroqKey" not in text
    assert PLANTED_GROQ_URL not in text  # the app never saw the real host
    exchange = report.scenarios[0].exchanges[0]
    assert exchange.path == "/openai/v1/responses"  # root base URL + /openai/v1 (K4)
