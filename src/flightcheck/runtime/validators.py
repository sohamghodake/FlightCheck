"""Runtime traffic checks (plan §17.4): one pure function per check ID.

Every validator takes ``(exchanges, app_run, scenario, config)`` and returns one
:class:`CheckResult` with ``verification=runtime_mock``. Validators never run anything and never
read the network; they only look at recorded traffic (already redacted by the recorder) and the
redacted :class:`AppRun`.

Status rules (ADR-018, plan §21.2, §20.14):

- ``passed`` needs at least one evidence item (the model enforces it) and a crisp, observed
  criterion; app-specific outcomes without an explicit expectation are ``needs_review``.
- No recorded traffic → every traffic check is ``skipped`` with the §17.2 reason.
- An incomplete recording (``TrafficRecorder.complete`` is false) cannot confirm an absence:
  a would-be ``passed`` becomes ``needs_review``; an observed failure stays ``failed``.
- An app with no tools under S04 → the tool checks are ``unsupported``.

The documented 400s come from ``core.contract.responses_contract_violations`` (shared with the
mock and the adapter), the limits from ``core.config``; nothing here re-implements them.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from functools import wraps
from typing import Any

from flightcheck.core.config import (
    BHARATCODE_DOCUMENTED_MODELS,
    BHARATCODE_PRO_ONLY_MODEL,
    MAX_OUTPUT_TOKENS_CAP,
    TestConfig,
)
from flightcheck.core.contract import responses_contract_violations
from flightcheck.core.models import (
    AppRun,
    CheckResult,
    CheckStatus,
    Confidence,
    Endpoint,
    Evidence,
    Finding,
    FindingKind,
    RecordedExchange,
    Severity,
    Verification,
)
from flightcheck.runtime.scenarios import (
    BUSY_STEP_PREFIX,
    EVERY_SCENARIO_CHECK_IDS,
    TOOL_STEP_ID,
    Scenario,
)

DOC_URL = "https://bharatcode.ai/build"
NO_TRAFFIC_REASON = (
    "no traffic observed — the app may be using a different base URL variable, failed before "
    "calling the API, or was not given input"
)
INCOMPLETE_NOTE = (
    "the mock's recording is incomplete ({missing} request(s) not recorded), so an absence "
    "cannot be confirmed"
)
MIN_BACKOFF_MS = 500  # FC-RT-041 threshold (plan §17.4)
SDK_RETRY_HEADER = "x-stainless-retry-count"  # sent by the OpenAI SDK (openai/_base_client.py:551)
_MAX_EVIDENCE = 10
_EXCERPT_CHARS = 300
_MODEL_ENDPOINTS = (Endpoint.RESPONSES, Endpoint.CHAT_COMPLETIONS)


@dataclass(frozen=True)
class CheckMeta:
    id: str
    title: str
    severity: Severity
    remediation: str


@dataclass(frozen=True)
class CheckConfig:
    """What a validator may know besides the traffic and the process result."""

    test: TestConfig
    recording_complete: bool = True
    missing_records: int = 0  # TrafficRecorder.dropped + record_failures
    aborted_for_limit: bool = False  # FlightCheck stopped the app after max_requests


Validator = Callable[
    [Sequence[RecordedExchange], AppRun | None, Scenario, CheckConfig], CheckResult
]

CHECKS: dict[str, CheckMeta] = {
    meta.id: meta
    for meta in (
        CheckMeta(
            "FC-RT-001",
            "store is false on /responses",
            Severity.HIGH,
            "Send store=false on every /responses request and replay history in input.",
        ),
        CheckMeta(
            "FC-RT-002",
            "No previous_response_id",
            Severity.HIGH,
            "Remove previous_response_id; the Responses API is stateless on BharatCode: replay "
            "prior turns and output items in input.",
        ),
        CheckMeta(
            "FC-RT-003",
            "Only function tools",
            Severity.HIGH,
            'Replace hosted tools with your own {"type": "function"} tools and run them in '
            "your app.",
        ),
        CheckMeta(
            "FC-RT-004",
            "No background mode",
            Severity.HIGH,
            "Remove background=true and run the request synchronously.",
        ),
        CheckMeta(
            "FC-RT-005",
            "Supported input modalities",
            Severity.HIGH,
            "Send text or image input only; audio, video and file input are not supported.",
        ),
        CheckMeta(
            "FC-RT-006",
            "Output budget ≤ 32 000",
            Severity.MEDIUM,
            f"Keep max_output_tokens / max_tokens at or below {MAX_OUTPUT_TOKENS_CAP}.",
        ),
        CheckMeta(
            "FC-RT-007",
            "Model in the documented catalogue",
            Severity.MEDIUM,
            "Use a documented model ID (qwen-3.8-27b, or deepseek-v4.1-flash on Pro) and "
            "always send model.",
        ),
        CheckMeta(
            "FC-RT-008",
            "Baseline run exits 0",
            Severity.INFO,
            "Check the app's stderr: under the baseline the mock answers normally, so a "
            "non-zero exit usually means a setup or parsing problem.",
        ),
        CheckMeta(
            "FC-RT-011",
            "App continues after a tool call",
            Severity.INFO,
            "After running a function call, send a follow-up request with its result.",
        ),
        CheckMeta(
            "FC-RT-012",
            "Tool result pairs with the issued call_id",
            Severity.HIGH,
            "Echo the call_id from the function_call item (Chat: tool_call_id from tool_calls) "
            "in the tool result; never hard-code it.",
        ),
        CheckMeta(
            "FC-RT-013",
            "Tool result output is a string",
            Severity.HIGH,
            "Serialise tool results to a string (for example json.dumps(result)) before "
            "sending them.",
        ),
        CheckMeta(
            "FC-RT-014",
            "Continuation replays context",
            Severity.HIGH,
            "In the follow-up, send the original input plus the response's output items "
            "(including the function_call) before the function_call_output, and resend the "
            "tool definitions you still need.",
        ),
        CheckMeta(
            "FC-RT-040",
            "Busy 503 is retried",
            Severity.MEDIUM,
            "Retry a 503 (model busy) after a pause, a bounded number of times.",
        ),
        CheckMeta(
            "FC-RT-041",
            "Retry backoff present",
            Severity.LOW,
            "Wait before retrying a 503; BharatCode says to wait a few seconds.",
        ),
        CheckMeta(
            "FC-RT-050",
            "Only the fake key was used",
            Severity.CRITICAL,
            "Read the API key only from the environment variable named in [test].api_key_env; "
            "do not load keys from files or source code.",
        ),
        CheckMeta(
            "FC-RT-060",
            "Request limit respected",
            Severity.HIGH,
            "Bound loops and retries so one run cannot send an unbounded number of requests.",
        ),
    )
}


# --- Shared helpers -------------------------------------------------------------------------


def by_seq(exchanges: Iterable[RecordedExchange]) -> list[RecordedExchange]:
    """Arrival order (a stream is appended to the recorder when it ends, not when it starts)."""
    return sorted(exchanges, key=lambda e: e.seq)


def model_exchanges(exchanges: Iterable[RecordedExchange]) -> list[RecordedExchange]:
    """Requests to ``/responses`` or ``/chat/completions`` (``/models`` and 404s excluded)."""
    return [e for e in by_seq(exchanges) if e.endpoint in _MODEL_ENDPOINTS]


def split_endpoints(
    exchanges: Iterable[RecordedExchange],
) -> tuple[list[RecordedExchange], list[RecordedExchange]]:
    """``(responses, chat)`` model requests that carried a JSON object body."""
    responses: list[RecordedExchange] = []
    chat: list[RecordedExchange] = []
    for exchange in model_exchanges(exchanges):
        if exchange.request_json is None:
            continue
        (responses if exchange.endpoint is Endpoint.RESPONSES else chat).append(exchange)
    return responses, chat


@dataclass(frozen=True)
class ToolResult:
    call_id: Any
    output: Any
    style: str  # "function_call_output" (Responses) or "tool_message" (Chat)


def tool_results(exchange: RecordedExchange) -> list[ToolResult]:
    """Tool results a request sends back: Responses ``function_call_output`` items in
    ``input``; Chat ``role: "tool"`` messages."""
    body = exchange.request_json or {}
    found: list[ToolResult] = []
    if exchange.endpoint is Endpoint.RESPONSES:
        items = body.get("input")
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and item.get("type") == "function_call_output":
                    found.append(
                        ToolResult(item.get("call_id"), item.get("output"), "function_call_output")
                    )
    elif exchange.endpoint is Endpoint.CHAT_COMPLETIONS:
        messages = body.get("messages")
        if isinstance(messages, list):
            for message in messages:
                if isinstance(message, dict) and message.get("role") == "tool":
                    found.append(
                        ToolResult(
                            message.get("tool_call_id"), message.get("content"), "tool_message"
                        )
                    )
    return found


def issued_call_ids(exchange: RecordedExchange) -> list[str]:
    ids = exchange.response_summary.get("call_ids")
    return [i for i in ids if isinstance(i, str)] if isinstance(ids, list) else []


def gaps_ms(exchanges: Sequence[RecordedExchange]) -> list[int]:
    """Time between consecutive exchanges (``at_ms``; recorded when each was answered)."""
    return [b.at_ms - a.at_ms for a, b in zip(exchanges, exchanges[1:], strict=False)]


def sdk_retry_count(exchange: RecordedExchange) -> int | None:
    """The OpenAI SDK's ``x-stainless-retry-count`` header (``None`` when absent/invalid)."""
    value = exchange.request_headers.get(SDK_RETRY_HEADER)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _short(value: Any) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        text = repr(value)
    return text if len(text) <= _EXCERPT_CHARS else text[: _EXCERPT_CHARS - 1] + "…"


def _traffic_evidence(exchange: RecordedExchange, excerpt: str = "", note: str = "") -> Evidence:
    line = f"{exchange.method} {exchange.path} → {exchange.response_status}"
    return Evidence(
        type="traffic",
        exchange_seq=exchange.seq,
        excerpt=f"{line} {excerpt}".strip(),
        note=note,
    )


def _checked_evidence(exchanges: Sequence[RecordedExchange], note: str) -> list[Evidence]:
    shown = [_traffic_evidence(e, note=note) for e in exchanges[:_MAX_EVIDENCE]]
    if len(exchanges) > _MAX_EVIDENCE:
        shown.append(
            Evidence(type="traffic", note=f"{len(exchanges) - _MAX_EVIDENCE} more not listed")
        )
    return shown


def _seqs(exchanges: Iterable[RecordedExchange]) -> str:
    return ", ".join(str(e.seq) for e in exchanges)


def _exit_text(app_run: AppRun | None) -> str:
    if app_run is None:
        return "the app did not run"
    if app_run.timed_out:
        return "the app timed out"
    return f"exit code {app_run.exit_code}"


def _result(
    check_id: str,
    scenario: Scenario,
    status: CheckStatus,
    message: str,
    *,
    reason: str | None = None,
    expected: str | None = None,
    observed: str | None = None,
    evidence: Sequence[Evidence] = (),
    severity: Severity | None = None,
) -> CheckResult:
    meta = CHECKS[check_id]
    return CheckResult(
        id=f"{scenario.id}:{check_id}",
        check_id=check_id,
        scenario_id=scenario.id,
        status=status,
        severity=severity or meta.severity,
        verification=Verification.RUNTIME_MOCK,
        message=message,
        reason=reason,
        expected=expected,
        observed=observed,
        evidence=list(evidence),
    )


def skipped(check_id: str, scenario: Scenario, reason: str) -> CheckResult:
    return _result(check_id, scenario, CheckStatus.SKIPPED, f"Skipped: {reason}.", reason=reason)


def _unsupported(check_id: str, scenario: Scenario, reason: str) -> CheckResult:
    return _result(
        check_id, scenario, CheckStatus.UNSUPPORTED, f"Unsupported: {reason}.", reason=reason
    )


def errored(check_id: str, scenario: Scenario, message: str) -> CheckResult:
    return _result(check_id, scenario, CheckStatus.ERROR, message)


def _traffic_check(check_id: str) -> Callable[[Validator], Validator]:
    """Zero traffic → skipped; incomplete recording → a pass becomes needs_review."""

    def decorate(fn: Validator) -> Validator:
        @wraps(fn)
        def wrapper(
            exchanges: Sequence[RecordedExchange],
            app_run: AppRun | None,
            scenario: Scenario,
            config: CheckConfig,
        ) -> CheckResult:
            ordered = by_seq(exchanges)
            if not ordered:
                if not config.recording_complete:
                    note = INCOMPLETE_NOTE.format(missing=config.missing_records)
                    return _result(
                        check_id,
                        scenario,
                        CheckStatus.NEEDS_REVIEW,
                        f"No exchange could be checked: {note}.",
                        observed=note,
                    )
                return skipped(check_id, scenario, NO_TRAFFIC_REASON)
            result = fn(ordered, app_run, scenario, config)
            if not config.recording_complete and result.status is CheckStatus.PASSED:
                note = INCOMPLETE_NOTE.format(missing=config.missing_records)
                return result.model_copy(
                    update={
                        "status": CheckStatus.NEEDS_REVIEW,
                        "message": f"{result.message} Not confirmed: {note}.",
                        "observed": note,
                    }
                )
            return result

        return wrapper

    return decorate


# --- FC-RT-001…007: the documented request contract -----------------------------------------

_RESPONSES_ONLY = "no /responses request with a JSON body was observed (this check applies to "


def _violating(exchanges: Sequence[RecordedExchange], field: str) -> list[RecordedExchange]:
    return [
        e
        for e in exchanges
        if e.request_json is not None
        and any(v.field == field for v in responses_contract_violations(e.request_json))
    ]


def _contract_field_check(
    check_id: str,
    field: str,
    exchanges: Sequence[RecordedExchange],
    scenario: Scenario,
    *,
    what: str,
    excerpt: Callable[[dict[str, Any]], Any],
) -> CheckResult:
    responses, _ = split_endpoints(exchanges)
    if not responses:
        return skipped(check_id, scenario, _RESPONSES_ONLY + "/responses)")
    bad = _violating(responses, field)
    if bad:
        return _result(
            check_id,
            scenario,
            CheckStatus.FAILED,
            f"{len(bad)} of {len(responses)} /responses request(s) sent {what} (seq "
            f"{_seqs(bad)}); BharatCode documents this as unsupported (HTTP 400), and the mock "
            "answered as documented.",
            expected=f"no {what}",
            observed=f"{what} in {len(bad)} request(s)",
            evidence=[
                _traffic_evidence(e, _short(excerpt(e.request_json or {})))
                for e in bad[:_MAX_EVIDENCE]
            ],
        )
    return _result(
        check_id,
        scenario,
        CheckStatus.PASSED,
        f"None of the {len(responses)} /responses request(s) sent {what}.",
        expected=f"no {what}",
        observed=f"{len(responses)} request(s) checked",
        evidence=_checked_evidence(responses, f"no {what}"),
    )


@_traffic_check("FC-RT-001")
def check_rt001(
    exchanges: Sequence[RecordedExchange],
    app_run: AppRun | None,
    scenario: Scenario,
    config: CheckConfig,
) -> CheckResult:
    cid = "FC-RT-001"
    responses, _ = split_endpoints(exchanges)
    if not responses:
        return skipped(cid, scenario, _RESPONSES_ONLY + "/responses)")
    store_true = _violating(responses, "store")
    other = [
        e
        for e in responses
        if e not in store_true
        and (e.request_json or {}).get("store") is not False
        and (e.request_json or {}).get("store") is not None
    ]
    omitted = [e for e in responses if (e.request_json or {}).get("store") is None]
    if store_true or other:
        bad = [*store_true, *other]
        return _result(
            cid,
            scenario,
            CheckStatus.FAILED,
            f"{len(bad)} of {len(responses)} /responses request(s) sent store other than false "
            f"(seq {_seqs(bad)}); BharatCode rejects store=true with HTTP 400 and keeps no "
            "server-side state.",
            expected="store=false",
            observed=f"store≠false in {len(bad)} request(s)",
            evidence=[
                _traffic_evidence(e, _short({"store": (e.request_json or {}).get("store")}))
                for e in bad[:_MAX_EVIDENCE]
            ],
        )
    if omitted:
        return _result(
            cid,
            scenario,
            CheckStatus.NEEDS_REVIEW,
            f"{len(omitted)} of {len(responses)} /responses request(s) omitted store (seq "
            f"{_seqs(omitted)}). BharatCode says to use store=false; what it does when store "
            "is omitted is [UNVERIFIED] until probe P-04.",
            expected="store=false",
            observed=f"store omitted in {len(omitted)} request(s)",
            evidence=[
                _traffic_evidence(e, "store omitted", note="send store=false explicitly")
                for e in omitted[:_MAX_EVIDENCE]
            ],
        )
    return _result(
        cid,
        scenario,
        CheckStatus.PASSED,
        f"All {len(responses)} /responses request(s) sent store=false.",
        expected="store=false",
        observed=f"store=false in {len(responses)} request(s)",
        evidence=_checked_evidence(responses, "store=false"),
    )


@_traffic_check("FC-RT-002")
def check_rt002(
    exchanges: Sequence[RecordedExchange],
    app_run: AppRun | None,
    scenario: Scenario,
    config: CheckConfig,
) -> CheckResult:
    return _contract_field_check(
        "FC-RT-002",
        "previous_response_id",
        exchanges,
        scenario,
        what="previous_response_id",
        excerpt=lambda body: {"previous_response_id": body.get("previous_response_id")},
    )


@_traffic_check("FC-RT-003")
def check_rt003(
    exchanges: Sequence[RecordedExchange],
    app_run: AppRun | None,
    scenario: Scenario,
    config: CheckConfig,
) -> CheckResult:
    return _contract_field_check(
        "FC-RT-003",
        "tools",
        exchanges,
        scenario,
        what="hosted (non-function) tools",
        excerpt=lambda body: {
            "tool_types": sorted(
                {
                    t["type"]
                    for t in body.get("tools") or []
                    if isinstance(t, dict) and isinstance(t.get("type"), str)
                }
            )
        },
    )


@_traffic_check("FC-RT-004")
def check_rt004(
    exchanges: Sequence[RecordedExchange],
    app_run: AppRun | None,
    scenario: Scenario,
    config: CheckConfig,
) -> CheckResult:
    return _contract_field_check(
        "FC-RT-004",
        "background",
        exchanges,
        scenario,
        what="background=true",
        excerpt=lambda body: {"background": body.get("background")},
    )


@_traffic_check("FC-RT-005")
def check_rt005(
    exchanges: Sequence[RecordedExchange],
    app_run: AppRun | None,
    scenario: Scenario,
    config: CheckConfig,
) -> CheckResult:
    def unsupported_input(body: dict[str, Any]) -> Any:
        found = [v.message for v in responses_contract_violations(body) if v.field == "input"]
        return found[0] if found else ""

    return _contract_field_check(
        "FC-RT-005",
        "input",
        exchanges,
        scenario,
        what="audio, video or file input",
        excerpt=unsupported_input,
    )


_BUDGET_KEYS = {
    Endpoint.RESPONSES: ("max_output_tokens",),
    Endpoint.CHAT_COMPLETIONS: ("max_tokens", "max_completion_tokens"),
}


@_traffic_check("FC-RT-006")
def check_rt006(
    exchanges: Sequence[RecordedExchange],
    app_run: AppRun | None,
    scenario: Scenario,
    config: CheckConfig,
) -> CheckResult:
    cid = "FC-RT-006"
    responses, chat = split_endpoints(exchanges)
    requests = by_seq([*responses, *chat])
    if not requests:
        return skipped(cid, scenario, "no /responses or /chat/completions request was observed")
    over: list[tuple[RecordedExchange, str, int]] = []
    odd: list[tuple[RecordedExchange, str, Any]] = []
    for exchange in requests:
        body = exchange.request_json or {}
        assert exchange.endpoint is not None
        for key in _BUDGET_KEYS[exchange.endpoint]:
            if key not in body or body[key] is None:
                continue
            value = body[key]
            if isinstance(value, int) and not isinstance(value, bool):
                if value > MAX_OUTPUT_TOKENS_CAP:
                    over.append((exchange, key, value))
            else:
                odd.append((exchange, key, value))
    if over:
        return _result(
            cid,
            scenario,
            CheckStatus.FAILED,
            f"{len(over)} request(s) asked for more than {MAX_OUTPUT_TOKENS_CAP} output tokens "
            f"(seq {_seqs(e for e, _, _ in over)}); BharatCode documents {MAX_OUTPUT_TOKENS_CAP} "
            "as the maximum.",
            expected=f"≤ {MAX_OUTPUT_TOKENS_CAP}",
            observed=", ".join(f"{k}={v}" for _, k, v in over[:_MAX_EVIDENCE]),
            evidence=[_traffic_evidence(e, _short({k: v})) for e, k, v in over[:_MAX_EVIDENCE]],
        )
    if odd:
        return _result(
            cid,
            scenario,
            CheckStatus.NEEDS_REVIEW,
            f"{len(odd)} request(s) sent an output budget that is not an integer (seq "
            f"{_seqs(e for e, _, _ in odd)}).",
            expected=f"an integer ≤ {MAX_OUTPUT_TOKENS_CAP}",
            observed=", ".join(f"{k}={_short(v)}" for _, k, v in odd[:_MAX_EVIDENCE]),
            evidence=[_traffic_evidence(e, _short({k: v})) for e, k, v in odd[:_MAX_EVIDENCE]],
        )
    return _result(
        cid,
        scenario,
        CheckStatus.PASSED,
        f"No request asked for more than {MAX_OUTPUT_TOKENS_CAP} output tokens "
        f"({len(requests)} checked).",
        expected=f"≤ {MAX_OUTPUT_TOKENS_CAP}",
        observed=f"{len(requests)} request(s) checked",
        evidence=_checked_evidence(requests, f"output budget ≤ {MAX_OUTPUT_TOKENS_CAP} or unset"),
    )


@_traffic_check("FC-RT-007")
def check_rt007(
    exchanges: Sequence[RecordedExchange],
    app_run: AppRun | None,
    scenario: Scenario,
    config: CheckConfig,
) -> CheckResult:
    cid = "FC-RT-007"
    responses, chat = split_endpoints(exchanges)
    requests = by_seq([*responses, *chat])
    if not requests:
        return skipped(cid, scenario, "no /responses or /chat/completions request was observed")
    missing = [e for e in requests if not isinstance((e.request_json or {}).get("model"), str)]
    unknown = [
        e
        for e in requests
        if e not in missing and (e.request_json or {})["model"] not in BHARATCODE_DOCUMENTED_MODELS
    ]
    pro_only = [
        e for e in requests if (e.request_json or {}).get("model") == BHARATCODE_PRO_ONLY_MODEL
    ]
    if missing or unknown:
        bad = by_seq([*missing, *unknown])
        models = sorted({_short((e.request_json or {}).get("model")) for e in bad})
        return _result(
            cid,
            scenario,
            CheckStatus.FAILED,
            f"{len(bad)} request(s) sent a model that is missing or not in BharatCode's "
            f"documented catalogue (seq {_seqs(bad)}): {', '.join(models)}. The model field is "
            "required on every request.",
            expected=" or ".join(sorted(BHARATCODE_DOCUMENTED_MODELS)),
            observed=", ".join(models),
            evidence=[
                _traffic_evidence(e, _short({"model": (e.request_json or {}).get("model")}))
                for e in bad[:_MAX_EVIDENCE]
            ],
        )
    if pro_only:
        return _result(
            cid,
            scenario,
            CheckStatus.NEEDS_REVIEW,
            f"{BHARATCODE_PRO_ONLY_MODEL} is documented but available on the Pro plan only "
            f"(seq {_seqs(pro_only)}); check that your plan includes it.",
            expected="a model your plan includes",
            observed=BHARATCODE_PRO_ONLY_MODEL,
            evidence=[_traffic_evidence(e, BHARATCODE_PRO_ONLY_MODEL) for e in pro_only[:10]],
            severity=Severity.INFO,
        )
    models = sorted({(e.request_json or {})["model"] for e in requests})
    return _result(
        cid,
        scenario,
        CheckStatus.PASSED,
        f"All {len(requests)} request(s) used a documented model: {', '.join(models)}.",
        expected=" or ".join(sorted(BHARATCODE_DOCUMENTED_MODELS)),
        observed=", ".join(models),
        evidence=_checked_evidence(requests, "documented model"),
    )


# --- FC-RT-008: baseline exit (S01) ---------------------------------------------------------


def check_rt008(
    exchanges: Sequence[RecordedExchange],
    app_run: AppRun | None,
    scenario: Scenario,
    config: CheckConfig,
) -> CheckResult:
    """S01 expects exit 0. Anything else is ``needs_review`` (never ``failed``): the mock
    answered normally, so the cause is app-specific."""
    cid = "FC-RT-008"
    if app_run is None:
        return skipped(cid, scenario, "the app did not run")
    process = Evidence(
        type="process",
        excerpt=app_run.stderr_excerpt[-_EXCERPT_CHARS:] if app_run.exit_code else "",
        note=f"{_exit_text(app_run)} after {app_run.duration_ms} ms",
    )
    if app_run.timed_out:
        return _result(
            cid,
            scenario,
            CheckStatus.NEEDS_REVIEW,
            f"The app did not finish within {config.test.timeout_s} s under the baseline.",
            expected="exit 0",
            observed="timed out",
            evidence=[process],
        )
    if app_run.exit_code != 0:
        return _result(
            cid,
            scenario,
            CheckStatus.NEEDS_REVIEW,
            f"The app exited with code {app_run.exit_code} under the baseline, where the mock "
            "answers normally; see its stderr.",
            expected="exit 0",
            observed=f"exit {app_run.exit_code}",
            evidence=[process],
        )
    if not model_exchanges(exchanges):
        return _result(
            cid,
            scenario,
            CheckStatus.NEEDS_REVIEW,
            f"The app exited 0 but no model request reached the mock ({NO_TRAFFIC_REASON}).",
            expected="exit 0 after at least one model request",
            observed="exit 0, no model request",
            evidence=[process],
        )
    return _result(
        cid,
        scenario,
        CheckStatus.PASSED,
        "The app exited 0 under the baseline.",
        expected="exit 0",
        observed="exit 0",
        evidence=[process],
    )


# --- FC-RT-011…014: tool round trip (S04) ---------------------------------------------------


@dataclass(frozen=True)
class _ToolRound:
    tool: RecordedExchange | None
    followups: list[RecordedExchange]
    blocked: CheckResult | None  # skipped/unsupported result when the round cannot be judged


def _tool_round(
    check_id: str, exchanges: Sequence[RecordedExchange], scenario: Scenario
) -> _ToolRound:
    model = model_exchanges(exchanges)
    if not model:
        return _ToolRound(
            None,
            [],
            skipped(check_id, scenario, "no /responses or /chat/completions request was observed"),
        )
    served = [e for e in model if e.step_id == TOOL_STEP_ID]
    if not served:
        first = model[0]
        return _ToolRound(
            None,
            [],
            skipped(
                check_id,
                scenario,
                f"the mock's tool-call step was never served (the first model request got HTTP "
                f"{first.response_status} from '{first.step_id}')",
            ),
        )
    tool = served[0]
    if tool.response_summary.get("no_tools"):
        return _ToolRound(
            tool,
            [],
            _unsupported(
                check_id,
                scenario,
                "app defines no tools: the request carried no function tool, so the mock "
                "answered with text",
            ),
        )
    if tool.response_summary.get("finish") != "tool_call" or not issued_call_ids(tool):
        return _ToolRound(
            tool,
            [],
            skipped(
                check_id,
                scenario,
                "the tool call did not reach the app (finish="
                f"{_short(tool.response_summary.get('finish'))})",
            ),
        )
    followups = [e for e in model if e.seq > tool.seq and e.request_json is not None]
    return _ToolRound(tool, followups, None)


@_traffic_check("FC-RT-011")
def check_rt011(
    exchanges: Sequence[RecordedExchange],
    app_run: AppRun | None,
    scenario: Scenario,
    config: CheckConfig,
) -> CheckResult:
    cid = "FC-RT-011"
    rnd = _tool_round(cid, exchanges, scenario)
    if rnd.blocked is not None:
        return rnd.blocked
    assert rnd.tool is not None
    issued = ", ".join(issued_call_ids(rnd.tool))
    if not rnd.followups:
        return _result(
            cid,
            scenario,
            CheckStatus.NEEDS_REVIEW,
            f"The mock returned a function call ({issued}) and the app sent no follow-up "
            f"request ({_exit_text(app_run)}). That is right only if the app does not intend "
            "to continue after a tool call.",
            expected="a follow-up request with the tool result",
            observed="no follow-up request",
            evidence=[_traffic_evidence(rnd.tool, f"call_ids={issued}")],
        )
    first = rnd.followups[0]
    return _result(
        cid,
        scenario,
        CheckStatus.PASSED,
        f"The app sent a follow-up request (seq {first.seq}) after the function call ({issued}).",
        expected="a follow-up request",
        observed=f"follow-up at seq {first.seq}",
        evidence=[_traffic_evidence(rnd.tool, f"call_ids={issued}"), _traffic_evidence(first)],
    )


@_traffic_check("FC-RT-012")
def check_rt012(
    exchanges: Sequence[RecordedExchange],
    app_run: AppRun | None,
    scenario: Scenario,
    config: CheckConfig,
) -> CheckResult:
    cid = "FC-RT-012"
    rnd = _tool_round(cid, exchanges, scenario)
    if rnd.blocked is not None:
        return rnd.blocked
    assert rnd.tool is not None
    issued = issued_call_ids(rnd.tool)
    if not rnd.followups:
        return skipped(cid, scenario, "no follow-up request after the tool call (see FC-RT-011)")
    for followup in rnd.followups:
        sent = {r.call_id for r in tool_results(followup)}
        if set(issued) <= sent:
            return _result(
                cid,
                scenario,
                CheckStatus.PASSED,
                f"The tool result in seq {followup.seq} echoes the issued call_id "
                f"{', '.join(issued)}.",
                expected=", ".join(issued),
                observed=", ".join(sorted(str(s) for s in sent)),
                evidence=[
                    _traffic_evidence(rnd.tool, f"issued call_ids={', '.join(issued)}"),
                    _traffic_evidence(followup, f"tool result call_ids={_short(sorted(sent))}"),
                ],
            )
    sent_any = [(f, r) for f in rnd.followups for r in tool_results(f)]
    if sent_any:
        seen = sorted({_short(r.call_id) for _, r in sent_any})
        message = (
            f"The tool result's call_id ({', '.join(seen)}) does not match the issued call_id "
            f"({', '.join(issued)}); BharatCode pairs a result with its call only by call_id."
        )
        evidence = [
            _traffic_evidence(f, f"tool result call_id={_short(r.call_id)}")
            for f, r in sent_any[:_MAX_EVIDENCE]
        ]
        observed = ", ".join(seen)
    else:
        message = (
            "No follow-up request carries a tool result (Responses: a function_call_output item "
            'in input; Chat: a role:"tool" message) for the issued call_id '
            f"({', '.join(issued)})."
        )
        evidence = [_traffic_evidence(f, "no tool result") for f in rnd.followups[:_MAX_EVIDENCE]]
        observed = "no tool result"
    return _result(
        cid,
        scenario,
        CheckStatus.FAILED,
        message,
        expected=", ".join(issued),
        observed=observed,
        evidence=[_traffic_evidence(rnd.tool, f"issued call_ids={', '.join(issued)}"), *evidence],
    )


@_traffic_check("FC-RT-013")
def check_rt013(
    exchanges: Sequence[RecordedExchange],
    app_run: AppRun | None,
    scenario: Scenario,
    config: CheckConfig,
) -> CheckResult:
    cid = "FC-RT-013"
    rnd = _tool_round(cid, exchanges, scenario)
    if rnd.blocked is not None:
        return rnd.blocked
    results = [(f, r) for f in rnd.followups for r in tool_results(f)]
    if not results:
        return skipped(cid, scenario, "no tool result was sent (see FC-RT-011 and FC-RT-012)")
    not_string = [(f, r) for f, r in results if not isinstance(r.output, str)]
    chat_parts = [
        (f, r) for f, r in not_string if r.style == "tool_message" and isinstance(r.output, list)
    ]
    wrong = [(f, r) for f, r in not_string if (f, r) not in chat_parts]
    if wrong:
        kinds = sorted({type(r.output).__name__ for _, r in wrong})
        return _result(
            cid,
            scenario,
            CheckStatus.FAILED,
            f"{len(wrong)} tool result(s) sent a {'/'.join(kinds)} instead of a string (seq "
            f"{_seqs(f for f, _ in wrong)}); BharatCode documents the function_call_output "
            "output as a string.",
            expected="a JSON string",
            observed=", ".join(kinds),
            evidence=[
                _traffic_evidence(f, _short({"call_id": r.call_id, "output": r.output}))
                for f, r in wrong[:_MAX_EVIDENCE]
            ],
        )
    if chat_parts:
        return _result(
            cid,
            scenario,
            CheckStatus.NEEDS_REVIEW,
            f"{len(chat_parts)} Chat tool message(s) sent content as a list of parts (seq "
            f"{_seqs(f for f, _ in chat_parts)}); BharatCode documents string output for "
            "Responses only, so list content on Chat is [UNVERIFIED].",
            expected="a JSON string",
            observed="list of content parts",
            evidence=[
                _traffic_evidence(f, _short({"tool_call_id": r.call_id}))
                for f, r in chat_parts[:_MAX_EVIDENCE]
            ],
        )
    return _result(
        cid,
        scenario,
        CheckStatus.PASSED,
        f"All {len(results)} tool result(s) were strings.",
        expected="a JSON string",
        observed="string",
        evidence=[_traffic_evidence(f, _short({"call_id": r.call_id})) for f, r in results[:10]],
    )


def user_texts(value: Any) -> list[str]:
    """User text in a Responses ``input`` (a string, or user message items) or Chat
    ``messages`` (``role: "user"``; string content or text parts)."""
    if isinstance(value, str):
        return [value]
    texts: list[str] = []
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, dict) or item.get("role") != "user":
                continue
            content = item.get("content")
            if isinstance(content, str):
                texts.append(content)
            elif isinstance(content, list):
                texts.extend(
                    part["text"]
                    for part in content
                    if isinstance(part, dict) and isinstance(part.get("text"), str)
                )
    return texts


def _has_tools(exchange: RecordedExchange) -> bool:
    tools = (exchange.request_json or {}).get("tools")
    return isinstance(tools, list) and len(tools) > 0


@_traffic_check("FC-RT-014")
def check_rt014(
    exchanges: Sequence[RecordedExchange],
    app_run: AppRun | None,
    scenario: Scenario,
    config: CheckConfig,
) -> CheckResult:
    cid = "FC-RT-014"
    rnd = _tool_round(cid, exchanges, scenario)
    if rnd.blocked is not None:
        return rnd.blocked
    assert rnd.tool is not None
    if not rnd.followups:
        return skipped(cid, scenario, "no follow-up request after the tool call (see FC-RT-011)")
    issued = issued_call_ids(rnd.tool)
    with_result = [f for f in rnd.followups if tool_results(f)]
    cont = with_result[0] if with_result else rnd.followups[0]
    body = cont.request_json or {}
    problems: list[str] = []
    unverified = ""
    if cont.endpoint is Endpoint.RESPONSES:
        items = body.get("input")
        original = user_texts((rnd.tool.request_json or {}).get("input"))
        replayed = user_texts(items)
        if any(text not in replayed for text in original):
            problems.append("the original user input")
        calls = [
            item
            for item in (items if isinstance(items, list) else [])
            if isinstance(item, dict)
            and item.get("type") == "function_call"
            and item.get("call_id") in issued
        ]
        if not calls:
            problems.append(f"the function_call item with call_id {', '.join(issued)}")
    else:
        messages = body.get("messages")
        messages = messages if isinstance(messages, list) else []
        assistant = [
            m
            for m in messages
            if isinstance(m, dict)
            and m.get("role") == "assistant"
            and any(
                isinstance(c, dict) and c.get("id") in issued for c in m.get("tool_calls") or []
            )
        ]
        if not assistant:
            problems.append(f"an assistant message whose tool_calls include {', '.join(issued)}")
        unverified = " (Chat: OpenAI-compatible convention; BharatCode's behaviour is [UNVERIFIED])"
    evidence = [_traffic_evidence(rnd.tool, f"issued call_ids={', '.join(issued)}")]
    if problems:
        return _result(
            cid,
            scenario,
            CheckStatus.FAILED,
            f"The continuation (seq {cont.seq}) does not replay {' and '.join(problems)}; "
            f"BharatCode keeps no state, so the follow-up must carry them{unverified}.",
            expected="original input + output items + tool result",
            observed="missing: " + "; ".join(problems),
            evidence=[*evidence, _traffic_evidence(cont, "context not replayed")],
        )
    if _has_tools(rnd.tool) and not _has_tools(cont):
        return _result(
            cid,
            scenario,
            CheckStatus.NEEDS_REVIEW,
            f"The continuation (seq {cont.seq}) replays the context but sends no tools; resend "
            f"tool definitions you still need{unverified}.",
            expected="tool definitions resent",
            observed="no tools in the follow-up",
            evidence=[*evidence, _traffic_evidence(cont, "no tools")],
        )
    return _result(
        cid,
        scenario,
        CheckStatus.PASSED,
        f"The continuation (seq {cont.seq}) replays the original input and the function call "
        f"{', '.join(issued)}{unverified}.",
        expected="original input + output items + tool result",
        observed="context replayed",
        evidence=[*evidence, _traffic_evidence(cont, "context replayed")],
    )


# --- FC-RT-040/041: busy 503 (S02) ----------------------------------------------------------


def _busy_attempts(
    check_id: str, exchanges: Sequence[RecordedExchange], scenario: Scenario
) -> tuple[list[RecordedExchange], CheckResult | None]:
    model = model_exchanges(exchanges)
    if not model:
        return [], skipped(
            check_id, scenario, "no /responses or /chat/completions request was observed"
        )
    busy = [e for e in model if e.step_id.startswith(BUSY_STEP_PREFIX)]
    if not busy:
        first = model[0]
        return [], skipped(
            check_id,
            scenario,
            f"no request reached the busy (503) step (the first model request got HTTP "
            f"{first.response_status} from '{first.step_id}')",
        )
    return [e for e in model if e.seq >= busy[0].seq], None


def _sdk_note(retries: Sequence[RecordedExchange]) -> str:
    counts = [sdk_retry_count(e) for e in retries]
    if counts and all(c is not None and c > 0 for c in counts):
        return (
            " The retries carry the OpenAI SDK's x-stainless-retry-count header "
            f"({', '.join(str(c) for c in counts)}): the SDK's built-in retries "
            "(DEFAULT_MAX_RETRIES = 2, openai/_constants.py) did the retrying, which may or may "
            "not be deliberate."
        )
    return ""


@_traffic_check("FC-RT-040")
def check_rt040(
    exchanges: Sequence[RecordedExchange],
    app_run: AppRun | None,
    scenario: Scenario,
    config: CheckConfig,
) -> CheckResult:
    cid = "FC-RT-040"
    attempts, blocked = _busy_attempts(cid, exchanges, scenario)
    if blocked is not None:
        return blocked
    if len(attempts) == 1:
        return _result(
            cid,
            scenario,
            CheckStatus.FAILED,
            f"Only one request was observed before the app stopped ({_exit_text(app_run)}): "
            "the documented busy 503 was not retried.",
            expected="≥ 2 attempts",
            observed="1 attempt",
            evidence=[_traffic_evidence(attempts[0], "503 busy, no retry")],
        )
    last = attempts[-1]
    return _result(
        cid,
        scenario,
        CheckStatus.PASSED,
        f"{len(attempts)} attempts after the busy 503 (seq {_seqs(attempts)}); the last got "
        f"HTTP {last.response_status} ({_exit_text(app_run)}).{_sdk_note(attempts[1:])}",
        expected="≥ 2 attempts",
        observed=f"{len(attempts)} attempts",
        evidence=_checked_evidence(attempts, "attempt"),
    )


@_traffic_check("FC-RT-041")
def check_rt041(
    exchanges: Sequence[RecordedExchange],
    app_run: AppRun | None,
    scenario: Scenario,
    config: CheckConfig,
) -> CheckResult:
    """Gaps between attempts. When every retry carries the SDK's retry-count header, the
    backoff is the SDK's own (0.5 s × 2^n minus up to 25 % jitter, so the first retry comes
    after 375–500 ms: openai/_base_client.py:898-920) and the result is ``needs_review``,
    whatever the measured gap: it is not the app's design, and BharatCode asks for a few
    seconds (ADR-029)."""
    cid = "FC-RT-041"
    attempts, blocked = _busy_attempts(cid, exchanges, scenario)
    if blocked is not None:
        return blocked
    if len(attempts) < 2:
        return skipped(cid, scenario, "no retry was observed (see FC-RT-040)")
    gaps = gaps_ms(attempts)
    shown = ", ".join(f"{g} ms" for g in gaps)
    evidence = _checked_evidence(attempts, "attempt")
    note = _sdk_note(attempts[1:])
    if note:
        return _result(
            cid,
            scenario,
            CheckStatus.NEEDS_REVIEW,
            f"Gaps between attempts: {shown}.{note} Its backoff starts at 375–500 ms; "
            "BharatCode asks clients to wait a few seconds before retrying a 503, so consider "
            "a longer backoff.",
            expected=f"each gap ≥ {MIN_BACKOFF_MS} ms",
            observed=shown,
            evidence=evidence,
        )
    short = [g for g in gaps if g < MIN_BACKOFF_MS]
    if short:
        return _result(
            cid,
            scenario,
            CheckStatus.FAILED,
            f"The app retried after {min(short)} ms (gaps: {shown}); wait before retrying a "
            "busy 503.",
            expected=f"each gap ≥ {MIN_BACKOFF_MS} ms",
            observed=shown,
            evidence=evidence,
        )
    return _result(
        cid,
        scenario,
        CheckStatus.PASSED,
        f"Every retry waited at least {MIN_BACKOFF_MS} ms (gaps: {shown}).",
        expected=f"each gap ≥ {MIN_BACKOFF_MS} ms",
        observed=shown,
        evidence=evidence,
    )


# --- FC-RT-050/060: key and request limit (every scenario) ----------------------------------


@_traffic_check("FC-RT-050")
def check_rt050(
    exchanges: Sequence[RecordedExchange],
    app_run: AppRun | None,
    scenario: Scenario,
    config: CheckConfig,
) -> CheckResult:
    """Uses only the recorder's ``auth`` flag; the header value is never stored or shown."""
    cid = "FC-RT-050"
    flagged = [e for e in exchanges if isinstance(e.response_summary.get("auth"), str)]
    if not flagged:
        return skipped(cid, scenario, "no request reached the mock's key check")

    def with_flag(flag: str) -> list[RecordedExchange]:
        return [e for e in flagged if e.response_summary["auth"] == flag]

    other, missing, unchecked = with_flag("other_key"), with_flag("missing"), with_flag("unchecked")
    if other:
        return _result(
            cid,
            scenario,
            CheckStatus.FAILED,
            f"{len(other)} request(s) carried an API key other than FlightCheck's fake key (seq "
            f"{_seqs(other)}): the app may load a real key from a file or from source code. "
            "The header value was not stored.",
            expected="FlightCheck's fake key",
            observed=f"another key in {len(other)} request(s)",
            evidence=[_traffic_evidence(e, "auth=other_key") for e in other[:_MAX_EVIDENCE]],
        )
    if missing:
        names = ", ".join(config.test.api_key_env)
        return _result(
            cid,
            scenario,
            CheckStatus.NEEDS_REVIEW,
            f"{len(missing)} request(s) sent no bearer token (seq {_seqs(missing)}); the app may "
            f"not read any of [test].api_key_env ({names}).",
            expected="FlightCheck's fake key",
            observed=f"no key in {len(missing)} request(s)",
            evidence=[_traffic_evidence(e, "auth=missing") for e in missing[:_MAX_EVIDENCE]],
        )
    if unchecked:
        return skipped(cid, scenario, "the mock ran without a fake key, so keys were not compared")
    return _result(
        cid,
        scenario,
        CheckStatus.PASSED,
        f"All {len(flagged)} request(s) used FlightCheck's fake key.",
        expected="FlightCheck's fake key",
        observed=f"fake key in {len(flagged)} request(s)",
        evidence=_checked_evidence(flagged, "auth=fake_key"),
    )


@_traffic_check("FC-RT-060")
def check_rt060(
    exchanges: Sequence[RecordedExchange],
    app_run: AppRun | None,
    scenario: Scenario,
    config: CheckConfig,
) -> CheckResult:
    cid = "FC-RT-060"
    limit = config.test.max_requests
    limited = [e for e in exchanges if e.step_id == "request_limit"]
    if limited or config.aborted_for_limit:
        stopped = " FlightCheck stopped the app." if config.aborted_for_limit else ""
        return _result(
            cid,
            scenario,
            CheckStatus.FAILED,
            f"The app sent more than max_requests ({limit}) requests; the mock answered "
            f"{len(limited)} of them with 429 flightcheck_request_limit (the OpenAI SDK retries "
            f"429 as well).{stopped}",
            expected=f"≤ {limit} requests",
            observed=f"{len(exchanges)} requests recorded",
            evidence=[
                _traffic_evidence(e, "429 flightcheck_request_limit")
                for e in limited[:_MAX_EVIDENCE]
            ]
            or [Evidence(type="process", note="stopped after exceeding max_requests")],
        )
    return _result(
        cid,
        scenario,
        CheckStatus.PASSED,
        f"{len(exchanges)} request(s), within max_requests ({limit}).",
        expected=f"≤ {limit} requests",
        observed=f"{len(exchanges)} requests",
        evidence=[Evidence(type="traffic", note=f"{len(exchanges)} exchanges recorded")],
    )


VALIDATORS: dict[str, Validator] = {
    "FC-RT-001": check_rt001,
    "FC-RT-002": check_rt002,
    "FC-RT-003": check_rt003,
    "FC-RT-004": check_rt004,
    "FC-RT-005": check_rt005,
    "FC-RT-006": check_rt006,
    "FC-RT-007": check_rt007,
    "FC-RT-008": check_rt008,
    "FC-RT-011": check_rt011,
    "FC-RT-012": check_rt012,
    "FC-RT-013": check_rt013,
    "FC-RT-014": check_rt014,
    "FC-RT-040": check_rt040,
    "FC-RT-041": check_rt041,
    "FC-RT-050": check_rt050,
    "FC-RT-060": check_rt060,
}


# --- Running and reporting ------------------------------------------------------------------


def evaluate(
    scenario: Scenario,
    exchanges: Sequence[RecordedExchange],
    app_run: AppRun | None,
    config: CheckConfig,
) -> list[CheckResult]:
    """Every check of ``scenario`` (its own, then the every-scenario checks), in order."""
    ordered = by_seq(exchanges)
    return [VALIDATORS[cid](ordered, app_run, scenario, config) for cid in scenario.all_check_ids]


def skip_all(scenario: Scenario, reason: str) -> list[CheckResult]:
    return [skipped(cid, scenario, reason) for cid in scenario.all_check_ids]


def error_all(scenario: Scenario, message: str) -> list[CheckResult]:
    return [errored(cid, scenario, message) for cid in scenario.all_check_ids]


_OBSERVED = (CheckStatus.PASSED, CheckStatus.FAILED, CheckStatus.NEEDS_REVIEW)


def report_once(
    runs: Sequence[tuple[Sequence[RecordedExchange], Sequence[CheckResult]]],
) -> list[list[CheckResult]]:
    """Report the every-scenario checks (FC-RT-001…007, 050, 060) once per run (ADR-029).

    Scenario-specific checks pass through unchanged. ``runs`` holds ``(exchanges, checks)`` per
    scenario, in run order. A failed or needs_review result that names offending requests is
    kept only when one of them was not already reported for that check; offenders are keyed by
    ``request_sha256`` (SDK retries and repeated scenarios send the same body), except for
    FC-RT-060, whose 429'd requests have no body and whose breach belongs to its scenario
    (keyed by scenario and seq). A failed result without offending requests is always kept. A
    passed result is kept only if no earlier observation of that check was kept; anything else
    (skipped, unsupported, error) only if nothing was kept for that check yet. So a scenario
    whose own checks were skipped is not reported as passed on the strength of a check that
    another scenario already showed.
    """
    reported: dict[str, set[str]] = {}
    observed: set[str] = set()
    kept_any: set[str] = set()
    out: list[list[CheckResult]] = []
    for exchanges, checks in runs:
        sha_of = {e.seq: e.request_sha256 for e in exchanges}
        kept: list[CheckResult] = []
        for check in checks:
            cid = check.check_id
            if cid not in EVERY_SCENARIO_CHECK_IDS:
                kept.append(check)
                continue
            offenders: set[str] = set()
            if check.status in (CheckStatus.FAILED, CheckStatus.NEEDS_REVIEW):
                offenders = {
                    f"{check.scenario_id}:{ev.exchange_seq}"
                    if cid == "FC-RT-060"
                    else sha_of[ev.exchange_seq]
                    for ev in check.evidence
                    if ev.type == "traffic" and ev.exchange_seq in sha_of
                }
            if offenders:
                seen = reported.setdefault(cid, set())
                if offenders <= seen:
                    continue
                seen |= offenders
            elif check.status is CheckStatus.FAILED:
                pass
            elif check.status is CheckStatus.PASSED:
                if cid in observed:
                    continue
            elif cid in kept_any:
                continue
            kept.append(check)
            kept_any.add(cid)
            if check.status in _OBSERVED:
                observed.add(cid)
        out.append(kept)
    return out


def to_finding(check: CheckResult) -> Finding:
    """Mirror a failed check as ``Finding(kind="runtime")`` (plan §21.2) for CI gating."""
    meta = CHECKS[check.check_id]
    medium = check.check_id == "FC-RT-014" and "[UNVERIFIED]" in check.message
    return Finding(
        id=Finding.make_id(check.check_id, check_ref=check.id),
        rule_id=check.check_id,
        title=meta.title,
        kind=FindingKind.RUNTIME,
        verification=Verification.RUNTIME_MOCK,
        severity=check.severity,
        confidence=Confidence.MEDIUM if medium else Confidence.HIGH,
        message=f"[{check.scenario_id}] {check.message}",
        location=None,
        evidence=list(check.evidence),
        remediation=meta.remediation,
        doc_url=DOC_URL,
        provider="bharatcode",
        check_ref=check.id,
    )


__all__ = [
    "CHECKS",
    "NO_TRAFFIC_REASON",
    "VALIDATORS",
    "CheckConfig",
    "CheckMeta",
    "ToolResult",
    "by_seq",
    "error_all",
    "evaluate",
    "gaps_ms",
    "issued_call_ids",
    "model_exchanges",
    "report_once",
    "sdk_retry_count",
    "skip_all",
    "split_endpoints",
    "to_finding",
    "tool_results",
    "user_texts",
]
