"""DiagnosticService: backends, consent, retries, fallbacks and the ADR-019 invariant."""

from __future__ import annotations

import json
from pathlib import Path

import httpx2
import pytest
from diag_support import (
    FAKE_KEY,
    batch_json,
    check,
    config,
    deterministic_part,
    exchange,
    explanation,
    finding,
    mock_transport,
    ok_text,
    report,
    scenario,
    service,
    steps,
)
from pydantic import SecretStr

from flightcheck.core.errors import ConsentRequiredError
from flightcheck.core.models import CheckStatus, FindingKind, Severity
from flightcheck.diagnostics import fake_llm
from flightcheck.diagnostics import service as service_module
from flightcheck.diagnostics.fake_llm import FAKE_PREFIX
from flightcheck.diagnostics.service import ExplainOptions
from flightcheck.mock.faults import MockAction as A


def _options(tmp_path: Path, consent=None, **kw) -> ExplainOptions:
    return ExplainOptions(repo=tmp_path, consent=consent, **kw)


def _yes(_info) -> bool:
    return True


def _two_groups():
    return report(
        [
            finding("FC-BC-001", line=10),
            finding("FC-BC-001", line=20),
            finding("FC-GEN-004", severity=Severity.MEDIUM, line=30),
        ]
    )


def _valid_answer(rep) -> str:
    groups: dict[str, list[str]] = {}
    for f in rep.findings:
        groups.setdefault(f.rule_id, []).append(f.id)
    return batch_json(*(explanation(rule, ids) for rule, ids in groups.items()))


# --- fake / none ----------------------------------------------------------------------------


def test_fake_backend_is_labelled_and_leaves_the_deterministic_part_alone(tmp_path):
    rep = _two_groups()
    before = rep.model_dump_json()
    out = service(config(tmp_path, llm="fake")).explain(rep, _options(tmp_path))
    assert rep.model_dump_json() == before  # the input is not mutated
    assert deterministic_part(out) == deterministic_part(rep)
    assert out.metadata == rep.metadata
    assert [s.rule_id for s in out.ai_suggestions] == ["FC-BC-001", "FC-GEN-004"]
    assert out.summary.ai_suggestions == 2
    for suggestion in out.ai_suggestions:
        assert suggestion.backend == "fake" and suggestion.model == fake_llm.FAKE_MODEL
        assert suggestion.structured_output == "n/a"
        exp = suggestion.explanation
        assert exp is not None
        texts = [exp.summary, exp.root_cause, exp.impact, *exp.fix_steps, *exp.caveats]
        assert all(text.startswith(FAKE_PREFIX) for text in texts)


def test_fake_and_none_never_ask_consent_or_build_an_adapter(tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("adapter built")

    monkeypatch.setattr(service_module, "build_adapter", boom)

    def consent(_info):
        raise AssertionError("consent asked")

    for llm in ("fake", "none"):
        out = service(config(tmp_path, llm=llm)).explain(_two_groups(), _options(tmp_path, consent))
        assert len(out.ai_suggestions) == 2


def test_none_backend_uses_the_deterministic_remediation(tmp_path):
    out = service(config(tmp_path, llm="none")).explain(_two_groups(), _options(tmp_path))
    assert {s.fallback_reason for s in out.ai_suggestions} == {"llm_none"}
    assert all(s.explanation is None and s.backend == "none" for s in out.ai_suggestions)
    assert out.ai_suggestions[0].remediation == "Send store=false."
    assert out.summary.ai_suggestions == 0


def test_default_backend_without_a_key_is_none_with_no_key(tmp_path):
    out = service(config(tmp_path, key=None)).explain(_two_groups(), _options(tmp_path))
    assert {(s.backend, s.fallback_reason) for s in out.ai_suggestions} == {("none", "no_key")}


def test_explicit_bharatcode_without_a_key_falls_back_without_consent(tmp_path, monkeypatch):
    monkeypatch.setattr(service_module, "build_adapter", lambda *a, **k: 1 / 0)
    out = service(config(tmp_path, key=None, llm="bharatcode")).explain(
        _two_groups(), _options(tmp_path, consent=lambda _i: 1 / 0)
    )
    assert {(s.backend, s.fallback_reason) for s in out.ai_suggestions} == {
        ("bharatcode", "no_key")
    }


# --- consent ---------------------------------------------------------------------------------


def test_default_backend_with_a_key_asks_consent_and_a_refusal_sends_nothing(tmp_path, monkeypatch):
    bridge = mock_transport()
    seen = []

    def refuse(info):
        seen.append(info)
        return False

    out = service(config(tmp_path), bridge.transport).explain(
        _two_groups(), _options(tmp_path, refuse)
    )
    assert len(seen) == 1 and bridge.requests == []
    info = seen[0]
    assert info.destination == "https://bharatcode.ai/api/model/v1/responses"
    assert info.model == "qwen-3.8-27b" and info.context == "metadata"
    assert (info.groups, info.findings, info.files) == (2, 3, [])
    assert info.characters > 0
    assert {s.fallback_reason for s in out.ai_suggestions} == {"consent_declined"}


def test_bharatcode_without_a_consent_callback_is_a_consent_error(tmp_path):
    bridge = mock_transport()
    with pytest.raises(ConsentRequiredError):
        service(config(tmp_path, llm="bharatcode"), bridge.transport).explain(
            _two_groups(), _options(tmp_path)
        )
    assert bridge.requests == []


def test_dry_run_asks_nothing_sends_nothing_and_changes_nothing(tmp_path):
    bridge = mock_transport()
    rep = _two_groups()
    svc = service(config(tmp_path, llm="bharatcode"), bridge.transport)
    options = _options(tmp_path, consent=lambda _i: 1 / 0, dry_run=True)
    out = svc.explain(rep, options)
    assert bridge.requests == [] and out == rep
    payloads = svc.prepare(rep, options).payloads()
    assert len(payloads) == 1 and payloads[0]["store"] is False


def test_network_disabled_is_a_fallback_not_a_crash(tmp_path):
    # FLIGHTCHECK_NO_NETWORK=1 (conftest) and no injected transport.
    out = service(config(tmp_path, llm="bharatcode")).explain(
        _two_groups(), _options(tmp_path, _yes)
    )
    assert {s.fallback_reason for s in out.ai_suggestions} == {"network_disabled"}
    assert all(s.backend == "bharatcode" for s in out.ai_suggestions)


# --- BharatCode through the mock -------------------------------------------------------------


def test_bharatcode_success_through_the_mock(tmp_path):
    rep = _two_groups()
    bridge = mock_transport(steps(ok_text(_valid_answer(rep))))
    out = service(config(tmp_path, llm="bharatcode"), bridge.transport).explain(
        rep, _options(tmp_path, _yes)
    )
    assert out.summary.ai_suggestions == 2
    first = out.ai_suggestions[0]
    assert first.explanation is not None and first.explanation.summary == "Summary for FC-BC-001"
    assert first.structured_output == "json_schema" and first.backend == "bharatcode"
    assert first.model == "qwen-3.8-27b" and first.latency_ms is not None
    assert first.fallback_reason is None and first.rejected_reason is None
    (body,) = bridge.bodies()
    assert body["store"] is False and body["max_output_tokens"] == 4000
    assert body["reasoning"] == {"effort": "none"}
    assert body["text"]["format"]["type"] == "json_schema"
    assert body["text"]["format"]["strict"] is True
    assert body["model"] == "qwen-3.8-27b"
    assert bridge.requests[0].headers["authorization"] == f"Bearer {FAKE_KEY}"
    assert FAKE_KEY not in json.dumps(body)
    assert deterministic_part(out) == deterministic_part(rep)


def test_model_and_budget_come_from_explain_config(tmp_path):
    rep = _two_groups()
    bridge = mock_transport(steps(ok_text(_valid_answer(rep))))
    cfg = config(
        tmp_path,
        llm="bharatcode",
        model="deepseek-v4.1-flash",
        max_output_tokens=1234,
        reasoning_none=False,
    )
    service(cfg, bridge.transport).explain(rep, _options(tmp_path, _yes))
    (body,) = bridge.bodies()
    assert body["model"] == "deepseek-v4.1-flash" and body["max_output_tokens"] == 1234
    assert "reasoning" not in body


def test_truncated_answer_retries_once_with_a_doubled_budget(tmp_path):
    rep = _two_groups()
    bridge = mock_transport(
        steps((A.TRUNCATE, {"text": '{"explanations": ['}), ok_text(_valid_answer(rep)))
    )
    out = service(config(tmp_path, llm="bharatcode"), bridge.transport).explain(
        rep, _options(tmp_path, _yes)
    )
    assert [b["max_output_tokens"] for b in bridge.bodies()] == [4000, 8000]
    assert out.summary.ai_suggestions == 2


def test_truncated_twice_falls_back_without_parsing_partial_json(tmp_path):
    rep = _two_groups()
    partial = _valid_answer(rep)  # complete JSON, but the response says it was cut off
    bridge = mock_transport(steps((A.TRUNCATE, {"text": partial}), (A.TRUNCATE, {"text": partial})))
    out = service(config(tmp_path, llm="bharatcode"), bridge.transport).explain(
        rep, _options(tmp_path, _yes)
    )
    assert len(bridge.requests) == 2
    assert {s.fallback_reason for s in out.ai_suggestions} == {"truncated"}
    assert all(s.explanation is None for s in out.ai_suggestions)


def test_truncated_at_the_documented_cap_is_not_retried(tmp_path):
    bridge = mock_transport(steps((A.TRUNCATE, {"text": "{"})))
    out = service(config(tmp_path, llm="bharatcode", max_output_tokens=32_000), bridge.transport)
    out = out.explain(_two_groups(), _options(tmp_path, _yes))
    assert len(bridge.requests) == 1
    assert {s.fallback_reason for s in out.ai_suggestions} == {"truncated"}


def test_busy_503_after_retries_is_provider_busy(tmp_path):
    bridge = mock_transport(steps(A.HTTP_ERROR, A.HTTP_ERROR, A.HTTP_ERROR))
    svc = service(config(tmp_path, llm="bharatcode"), bridge.transport)
    out = svc.explain(_two_groups(), _options(tmp_path, _yes))
    assert len(bridge.requests) == 3  # the adapter's RetryPolicy, with the injected sleep
    assert {s.fallback_reason for s in out.ai_suggestions} == {"provider_busy"}
    assert "busy" in (out.ai_suggestions[0].rejected_reason or "")


@pytest.mark.parametrize("status", [401, 403])
def test_auth_errors_are_auth_or_plan(tmp_path, status):
    bridge = mock_transport(steps((A.HTTP_ERROR, {"status": status})))
    out = service(config(tmp_path, llm="bharatcode"), bridge.transport).explain(
        _two_groups(), _options(tmp_path, _yes)
    )
    assert len(bridge.requests) == 1
    assert {s.fallback_reason for s in out.ai_suggestions} == {"auth_or_plan"}


def test_400_on_text_format_retries_once_prompt_only(tmp_path):
    rep = _two_groups()
    bridge = mock_transport(steps((A.HTTP_ERROR, {"status": 400}), ok_text(_valid_answer(rep))))
    out = service(config(tmp_path, llm="bharatcode"), bridge.transport).explain(
        rep, _options(tmp_path, _yes)
    )
    first, second = bridge.bodies()
    assert "text" in first and "text" not in second
    assert "Respond with one JSON object" in second["instructions"]
    assert {s.structured_output for s in out.ai_suggestions} == {"prompt_only"}
    assert out.summary.ai_suggestions == 2


def test_400_twice_is_a_provider_error(tmp_path):
    bridge = mock_transport(steps((A.HTTP_ERROR, {"status": 400}), (A.HTTP_ERROR, {"status": 400})))
    out = service(config(tmp_path, llm="bharatcode"), bridge.transport).explain(
        _two_groups(), _options(tmp_path, _yes)
    )
    assert len(bridge.requests) == 2
    assert {(s.fallback_reason, s.structured_output) for s in out.ai_suggestions} == {
        ("provider_error", "prompt_only")
    }


def test_timeout_is_a_fallback(tmp_path):
    def handler(request):
        raise httpx2.ReadTimeout("slow", request=request)

    out = service(config(tmp_path, llm="bharatcode"), httpx2.MockTransport(handler)).explain(
        _two_groups(), _options(tmp_path, _yes)
    )
    assert {s.fallback_reason for s in out.ai_suggestions} == {"timeout"}


def test_unexpected_backend_exception_becomes_internal_error(tmp_path, monkeypatch):
    def broken(self, request):
        raise RuntimeError("boom")

    monkeypatch.setattr(fake_llm.FakeLLM, "respond", broken)
    out = service(config(tmp_path, llm="fake")).explain(_two_groups(), _options(tmp_path))
    assert {s.fallback_reason for s in out.ai_suggestions} == {"internal_error"}


# --- limits ----------------------------------------------------------------------------------


def _many_groups(n: int):
    return report([finding(f"FC-BC-00{i}", line=i) for i in range(1, n + 1)])


def test_call_cap_splits_groups_and_stops_at_max_calls(tmp_path):
    rep = _many_groups(5)
    answers = [ok_text(batch_json(explanation(f.rule_id, [f.id]))) for f in rep.findings]
    bridge = mock_transport(steps(*answers))
    svc = service(config(tmp_path, llm="bharatcode"), bridge.transport, input_cap=1500)
    plan = svc.prepare(rep, _options(tmp_path, _yes))
    assert len(plan.calls) == 3 and all(len(c.groups) == 1 for c in plan.calls)
    out = svc.explain(rep, _options(tmp_path, _yes), plan=plan)
    assert len(bridge.requests) == 3
    reasons = [s.fallback_reason for s in out.ai_suggestions]
    assert reasons == [None, None, None, "call_limit", "call_limit"]


def test_groups_share_a_call_under_the_input_cap(tmp_path):
    plan = service(config(tmp_path, llm="fake")).prepare(_many_groups(5), _options(tmp_path))
    assert len(plan.calls) == 1 and len(plan.calls[0].groups) == 5


def test_group_larger_than_the_input_cap_is_skipped(tmp_path):
    svc = service(config(tmp_path, llm="fake"), input_cap=100)
    out = svc.explain(_two_groups(), _options(tmp_path))
    assert {s.fallback_reason for s in out.ai_suggestions} == {"input_too_large"}


# --- test-command reports ---------------------------------------------------------------------


def test_test_report_with_runtime_findings_and_needs_review_checks(tmp_path):
    failed = check(
        "FC-RT-012", "S04", status=CheckStatus.FAILED, severity=Severity.HIGH, exchange_seq=1
    )
    review = check("FC-RT-020", "S03")
    passed = check("FC-RT-001", "S01", status=CheckStatus.PASSED)
    runtime = finding(
        "FC-RT-012",
        kind=FindingKind.RUNTIME,
        path=None,
        check_ref=failed.id,
        title="Tool result pairs with the issued call_id",
    )
    rep = report(
        [runtime],
        [
            scenario("S04", [failed], [exchange(1, {"input": "x"})]),
            scenario("S03", [review]),
            scenario("S01", [passed]),
        ],
        command="test",
    )
    out = service(config(tmp_path, llm="fake")).explain(rep, _options(tmp_path))
    assert [s.rule_id for s in out.ai_suggestions] == ["FC-RT-012", "FC-RT-020"]
    assert out.ai_suggestions[0].finding_ids == [runtime.id]  # the failed check is mirrored
    assert out.ai_suggestions[1].finding_ids == ["S03:FC-RT-020"]
    assert out.ai_suggestions[1].title == "Truncation surfaced"  # from the injected catalogue
    assert out.ai_suggestions[1].remediation
    assert deterministic_part(out) == deterministic_part(rep)
    statuses = [c.status for s in out.scenarios for c in s.checks]
    assert statuses == [CheckStatus.FAILED, CheckStatus.NEEDS_REVIEW, CheckStatus.PASSED]


def test_zero_findings_plan_has_nothing_to_explain(tmp_path):
    svc = service(config(tmp_path, llm="fake"))
    rep = report([], [scenario("S01", [check("FC-RT-001", "S01", status=CheckStatus.PASSED)])])
    assert svc.prepare(rep, _options(tmp_path)).nothing_to_explain
    assert svc.explain(rep, _options(tmp_path)).ai_suggestions == []


def test_reexplaining_replaces_previous_suggestions(tmp_path):
    svc = service(config(tmp_path, llm="fake"))
    once = svc.explain(_two_groups(), _options(tmp_path))
    twice = svc.explain(once, _options(tmp_path))
    assert len(twice.ai_suggestions) == 2
    assert deterministic_part(twice) == deterministic_part(once)


def test_provider_kind_without_an_adapter_is_provider_error(tmp_path):
    (tmp_path / "flightcheck.toml").write_text(
        '[explain]\nprovider = "other"\nllm = "bharatcode"\n'
        '[providers.other]\nkind = "openai_compatible"\nbase_url = "https://example.test/v1"\n'
        'api_key_env = "OTHER_KEY"\n'
    )
    cfg = config(tmp_path, key=None)
    cfg.providers["other"].api_key = SecretStr("other-key-1234567")
    out = service(cfg).explain(_two_groups(), _options(tmp_path, _yes))
    assert {s.fallback_reason for s in out.ai_suggestions} == {"provider_error"}
    assert "adapter" in (out.ai_suggestions[0].rejected_reason or "")
