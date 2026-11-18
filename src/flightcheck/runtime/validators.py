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
- An app with no tools under S04/S05 → the tool checks are ``unsupported``.
- FC-RT-020/030 (S03/S06) judge the process outcome only against an explicit ``[test.expect]``
  value, parsed once into :class:`Expectation` (ADR-030); without one they are
  ``needs_review``, and a timeout never decides them. FC-RT-022/023 are traffic checks.

The documented 400s come from ``core.contract`` (shared with the mock and the adapter), the
limits from ``core.config``; nothing here re-implements them.

Provider (ADR-038): ``CheckConfig.provider`` is the run's provider. BharatCode runs keep every
message and status rule unchanged. Groq runs evaluate the Groq every-scenario checks
(``scenarios.GROQ_EVERY_SCENARIO_CHECK_IDS``), judge FC-RT-006/007 against Groq's dated catalogue,
and word messages from docs/GROQ_CONTRACT.md (row IDs cited; [UNVERIFIED] rows stay labelled).
Titles, remediation and documentation links per provider come from :func:`meta_for`.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from functools import wraps
from typing import Any

from flightcheck.core.config import (
    BHARATCODE_DOCUMENTED_MODELS,
    BHARATCODE_PRO_ONLY_MODEL,
    GROQ_CATALOGUE_DATE,
    GROQ_DECOMMISSIONED_MODELS,
    GROQ_MODELS,
    MAX_OUTPUT_TOKENS_CAP,
    ExpectConfig,
    RuntimeProvider,
    TestConfig,
)
from flightcheck.core.contract import (
    GROQ_BUILTIN_TOOL_TYPES,
    GROQ_DOCUMENTED_TOOL_TYPES,
    Violation,
    groq_responses_contract_violations,
    responses_contract_violations,
)
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
from flightcheck.runtime.harness import TRUNCATION_MARK
from flightcheck.runtime.scenarios import (
    BUSY_STEP_PREFIX,
    RATE_LIMIT_STEP_ID,
    REPORTED_ONCE_CHECK_IDS,
    STREAM_FAIL_STEP_ID,
    TOOL_STEP_ID,
    TRUNCATE_STEP_ID,
    TRUNCATED_TOOL_STEP_ID,
    TRUNCATION_STEP_IDS,
    Scenario,
)

DOC_URL = "https://bharatcode.ai/build"
# Groq pages each check's contract rows come from (docs/GROQ_CONTRACT.md "Sources").
_GROQ_DOCS = "https://console.groq.com/docs"
GROQ_DOC_RESPONSES = f"{_GROQ_DOCS}/responses-api"
GROQ_DOC_MODELS = f"{_GROQ_DOCS}/models"
GROQ_DOC_API = f"{_GROQ_DOCS}/api-reference"
GROQ_DOC_LOCAL_TOOLS = f"{_GROQ_DOCS}/tool-use/local-tool-calling"
GROQ_DOC_BUILTIN = f"{_GROQ_DOCS}/tool-use/built-in-tools"
GROQ_DOC_ERRORS = f"{_GROQ_DOCS}/errors"
GROQ_DOC_RATE_LIMITS = f"{_GROQ_DOCS}/rate-limits"
GROQ_DOC_SECURITY = f"{_GROQ_DOCS}/production-readiness/security-onboarding"
GROQ_DOC_CHECKLIST = f"{_GROQ_DOCS}/production-readiness/production-ready-checklist"
GROQ_DATE = GROQ_CATALOGUE_DATE
BHARATCODE_ONLY: frozenset[str] = frozenset({"bharatcode"})
GROQ_ONLY: frozenset[str] = frozenset({"groq"})
BOTH: frozenset[str] = frozenset({"bharatcode", "groq"})
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
    """One check. ``title``/``remediation`` are the BharatCode texts (or the only texts of a
    Groq-only check); ``groq_*`` replace them on Groq runs when set. ``providers`` is the
    gating table's row (ADR-038): the runs the check is evaluated on."""

    id: str
    title: str
    severity: Severity
    remediation: str
    providers: frozenset[str] = BOTH
    groq_title: str | None = None
    groq_remediation: str | None = None
    groq_doc_url: str | None = None


class ExpectKind(StrEnum):
    NONZERO_EXIT = "nonzero_exit"
    STDOUT_CONTAINS = "stdout_contains"
    STDERR_CONTAINS = "stderr_contains"
    ANY = "any"


@dataclass(frozen=True)
class Expectation:
    """One ``[test.expect]`` value parsed once (ADR-030). ``raw`` is the configured string."""

    kind: ExpectKind
    text: str = ""
    raw: str = ""

    @classmethod
    def parse(cls, raw: str) -> Expectation:
        """``nonzero_exit`` | ``any`` | ``stdout_contains:<text>`` | ``stderr_contains:<text>``.

        core's ``_EXPECT_PATTERN`` already validated config values; this raises ``ValueError``
        for anything else, so a value that bypassed config is never guessed at.
        """
        if raw in (ExpectKind.NONZERO_EXIT.value, ExpectKind.ANY.value):
            return cls(ExpectKind(raw), raw=raw)
        for kind in (ExpectKind.STDOUT_CONTAINS, ExpectKind.STDERR_CONTAINS):
            prefix = f"{kind.value}:"
            if raw.startswith(prefix) and len(raw) > len(prefix):
                return cls(kind, text=raw[len(prefix) :], raw=raw)
        raise ValueError(
            f"invalid expectation {raw!r}: use nonzero_exit, any, stdout_contains:<text> or "
            "stderr_contains:<text>"
        )


@dataclass(frozen=True)
class Expectations:
    on_truncation: Expectation | None = None
    on_stream_failure: Expectation | None = None
    on_busy_exhausted: Expectation | None = None  # S02b (P1); parsed, not used yet

    @classmethod
    def from_config(cls, expect: ExpectConfig | None) -> Expectations:
        if expect is None:
            return cls()

        def one(raw: str | None) -> Expectation | None:
            return None if raw is None else Expectation.parse(raw)

        return cls(
            on_truncation=one(expect.on_truncation),
            on_stream_failure=one(expect.on_stream_failure),
            on_busy_exhausted=one(expect.on_busy_exhausted),
        )


@dataclass(frozen=True)
class CheckConfig:
    """What a validator may know besides the traffic and the process result."""

    test: TestConfig
    recording_complete: bool = True
    missing_records: int = 0  # TrafficRecorder.dropped + record_failures
    aborted_for_limit: bool = False  # FlightCheck stopped the app after max_requests
    provider: RuntimeProvider = "bharatcode"  # the run's provider (ADR-038)
    expectations: Expectations = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "expectations", Expectations.from_config(self.test.expect))


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
            providers=BHARATCODE_ONLY,
        ),
        CheckMeta(
            "FC-RT-002",
            "No previous_response_id",
            Severity.HIGH,
            "Remove previous_response_id; the Responses API is stateless on BharatCode: replay "
            "prior turns and output items in input.",
            groq_remediation="Remove previous_response_id: Groq lists it as not supported on "
            "/responses (contract R4) and the API is not stateful (R3), so replay prior turns "
            "and output items in input.",
            groq_doc_url=GROQ_DOC_RESPONSES,
        ),
        CheckMeta(
            "FC-RT-003",
            "Only function tools",
            Severity.HIGH,
            'Replace hosted tools with your own {"type": "function"} tools and run them in '
            "your app.",
            providers=BHARATCODE_ONLY,
        ),
        CheckMeta(
            "FC-RT-004",
            "No background mode",
            Severity.HIGH,
            "Remove background=true and run the request synchronously.",
            providers=BHARATCODE_ONLY,
        ),
        CheckMeta(
            "FC-RT-005",
            "Supported input modalities",
            Severity.HIGH,
            "Send text or image input only; audio, video and file input are not supported.",
            providers=BHARATCODE_ONLY,
        ),
        CheckMeta(
            "FC-RT-006",
            "Output budget ≤ 32 000",
            Severity.MEDIUM,
            f"Keep max_output_tokens / max_tokens at or below {MAX_OUTPUT_TOKENS_CAP}.",
            groq_title="Output budget within the model's maximum",
            groq_remediation="Keep max_completion_tokens (Chat) / max_output_tokens (Responses) "
            "within the model's documented maximum completion tokens (Groq's models page; "
            f"FlightCheck's catalogue is dated {GROQ_DATE}).",
            groq_doc_url=GROQ_DOC_MODELS,
        ),
        CheckMeta(
            "FC-RT-007",
            "Model in the documented catalogue",
            Severity.MEDIUM,
            "Use a documented model ID (qwen-3.8-27b, or deepseek-v4.1-flash on Pro) and "
            "always send model.",
            groq_title="Model in Groq's catalogue",
            groq_remediation="Always send model, and use a model from Groq's catalogue that is "
            "not decommissioned (GET /models lists the models active for your key, contract "
            f"M2, M6; FlightCheck's catalogue is dated {GROQ_DATE}).",
            groq_doc_url=GROQ_DOC_MODELS,
        ),
        CheckMeta(
            "FC-RT-008",
            "Baseline run exits 0",
            Severity.INFO,
            "Check the app's stderr: under the baseline the mock answers normally, so a "
            "non-zero exit usually means a setup or parsing problem.",
        ),
        CheckMeta(
            "FC-RT-009",
            "store is not true on /responses",
            Severity.HIGH,
            "Omit store or send store=false on /responses: Groq accepts only false or null "
            "(contract R5) and keeps no state, so replay the conversation in input (R3).",
            providers=GROQ_ONLY,
            groq_doc_url=GROQ_DOC_RESPONSES,
        ),
        CheckMeta(
            "FC-RT-011",
            "App continues after a tool call",
            Severity.INFO,
            "After running a function call, send a follow-up request with its result.",
            groq_remediation="After running a function call, send a follow-up request with "
            'its result (Chat: a role "tool" message with the call\'s tool_call_id, contract '
            "T5).",
            groq_doc_url=GROQ_DOC_LOCAL_TOOLS,
        ),
        CheckMeta(
            "FC-RT-012",
            "Tool result pairs with the issued call_id",
            Severity.HIGH,
            "Echo the call_id from the function_call item (Chat: tool_call_id from tool_calls) "
            "in the tool result; never hard-code it.",
            groq_remediation="Echo the call's id as the tool message's tool_call_id (Groq "
            "requires them to match, contract T5; on /responses echo the function_call's "
            "call_id, the OpenAI convention, [UNVERIFIED] for Groq: T10); never hard-code it.",
            groq_doc_url=GROQ_DOC_LOCAL_TOOLS,
        ),
        CheckMeta(
            "FC-RT-013",
            "Tool result output is a string",
            Severity.HIGH,
            "Serialise tool results to a string (for example json.dumps(result)) before "
            "sending them.",
            groq_remediation="Send tool results as strings, for example json.dumps(result), "
            "as Groq recommends (contract T5).",
            groq_doc_url=GROQ_DOC_LOCAL_TOOLS,
        ),
        CheckMeta(
            "FC-RT-014",
            "Continuation replays context",
            Severity.HIGH,
            "In the follow-up, send the original input plus the response's output items "
            "(including the function_call) before the function_call_output, and resend the "
            "tool definitions you still need.",
            groq_remediation="In the follow-up, replay the conversation: on Chat the assistant "
            "message with its tool_calls, then one tool message per call (contract T4, T5); on "
            "/responses the original input plus the output items before the "
            "function_call_output (Groq's Responses API is not stateful, R3). Resend the tool "
            "definitions you still need.",
            groq_doc_url=GROQ_DOC_LOCAL_TOOLS,
        ),
        CheckMeta(
            "FC-RT-015",
            "Only tools the model supports",
            Severity.HIGH,
            "Use function tools you run yourself; Groq's built-in browser_search and "
            "code_interpreter work only on openai/gpt-oss-20b and openai/gpt-oss-120b (contract "
            f"R9, T8; models as listed on {GROQ_DATE}).",
            providers=GROQ_ONLY,
            groq_doc_url=GROQ_DOC_BUILTIN,
        ),
        CheckMeta(
            "FC-RT-020",
            "Truncation surfaced",
            Severity.MEDIUM,
            'Check status == "incomplete" (Chat: finish_reason == "length"), keep the partial '
            "output and surface the truncation (for example exit non-zero); set [test.expect] "
            "on_truncation so FlightCheck can verify it.",
            groq_remediation='Check finish_reason == "length" (Chat) or status == "incomplete" '
            "(Responses, where the budget includes reasoning tokens: contract C11, R10), keep "
            "the partial output and surface the truncation (for example exit non-zero); set "
            "[test.expect] on_truncation so FlightCheck can verify it.",
            groq_doc_url=GROQ_DOC_API,
        ),
        CheckMeta(
            "FC-RT-022",
            "Incomplete tool call not executed",
            Severity.HIGH,
            'Do not run a function call from a truncated response (status "incomplete" / '
            'finish_reason "length"): its arguments may be cut off. Raise the output budget '
            "or ask again instead.",
            groq_remediation="FlightCheck recommendation: do not run a function call from a "
            'truncated response (finish_reason "length" / status "incomplete"), since its '
            "arguments may be cut off; raise the output budget or ask again. Groq may instead "
            "reject an invalid generated tool call with HTTP 400 and error.failed_generation "
            "(contract T6): handle that too.",
            groq_doc_url=GROQ_DOC_LOCAL_TOOLS,
        ),
        CheckMeta(
            "FC-RT-023",
            "No identical retry after truncation",
            Severity.MEDIUM,
            "Do not resend the same request after a truncation (the same budget runs out "
            "again): raise max_output_tokens, shorten the input or ask for a shorter answer.",
            groq_remediation="FlightCheck recommendation: do not resend the same request after "
            "a truncation, since the same budget runs out again; raise max_completion_tokens / "
            "max_output_tokens, shorten the input or ask for a shorter answer.",
        ),
        CheckMeta(
            "FC-RT-030",
            "Stream failure surfaced",
            Severity.MEDIUM,
            "Treat response.failed, an error event or a stream that ends without a terminal "
            "event as a failure: discard the partial output, then surface the error or retry; "
            "set [test.expect] on_stream_failure so FlightCheck can verify it.",
            groq_remediation="Treat a Chat stream that ends without a finish_reason or brings a "
            "chunk with x_groq.error (contract ST3), and on /responses a response.failed event "
            "or a stream without a terminal event, as a failure: discard the partial output, "
            "then surface the error or retry (Groq asks apps to handle streaming errors "
            "gracefully, ST5); set [test.expect] on_stream_failure so FlightCheck can verify "
            "it.",
            groq_doc_url=GROQ_DOC_CHECKLIST,
        ),
        CheckMeta(
            "FC-RT-040",
            "Busy 503 is retried",
            Severity.MEDIUM,
            "Retry a 503 (model busy) after a pause, a bounded number of times.",
            groq_title="503 is retried",
            groq_remediation="Retry a 503 after a pause (Groq's errors page says to wait before "
            "retrying, contract E3), a bounded number of times.",
            groq_doc_url=GROQ_DOC_ERRORS,
        ),
        CheckMeta(
            "FC-RT-041",
            "Retry backoff present",
            Severity.LOW,
            "Wait before retrying a 503; BharatCode says to wait a few seconds.",
            groq_remediation="Wait before retrying: Groq recommends exponential backoff with "
            "jitter for 429 and 5xx errors (contract E9).",
            groq_doc_url=GROQ_DOC_SECURITY,
        ),
        CheckMeta(
            "FC-RT-044",
            "429 retry waits for retry-after",
            Severity.MEDIUM,
            "After a 429, wait at least the retry-after seconds before the next request (Groq "
            "sets retry-after only on 429, contract E5); the OpenAI and groq SDKs honour it in "
            "their built-in retries (K6).",
            providers=GROQ_ONLY,
            groq_doc_url=GROQ_DOC_RATE_LIMITS,
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


def meta_for(check_id: str, provider: RuntimeProvider) -> tuple[str, str, str | None]:
    """``(title, remediation, doc_url)`` of a check on a run of ``provider`` (ADR-038).
    BharatCode runs get the original texts and ``DOC_URL``; Groq runs the ``groq_*`` texts
    where set and the Groq page the check's contract rows come from (``None`` for checks with
    no Groq basis, such as FlightCheck's own recommendations)."""
    meta = CHECKS[check_id]
    if provider != "groq":
        return meta.title, meta.remediation, DOC_URL
    return (
        meta.groq_title or meta.title,
        meta.groq_remediation or meta.remediation,
        meta.groq_doc_url,
    )


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


def _violations(body: dict[str, Any], provider: RuntimeProvider = "bharatcode") -> list[Violation]:
    if provider == "groq":
        return groq_responses_contract_violations(body)
    return responses_contract_violations(body)


def _violating(
    exchanges: Sequence[RecordedExchange], field: str, provider: RuntimeProvider = "bharatcode"
) -> list[RecordedExchange]:
    return [
        e
        for e in exchanges
        if e.request_json is not None
        and any(v.field == field for v in _violations(e.request_json, provider))
    ]


def _contract_field_check(
    check_id: str,
    field: str,
    exchanges: Sequence[RecordedExchange],
    scenario: Scenario,
    *,
    what: str,
    excerpt: Callable[[dict[str, Any]], Any],
    provider: RuntimeProvider = "bharatcode",
    groq_basis: str = "",
) -> CheckResult:
    responses, _ = split_endpoints(exchanges)
    if not responses:
        return skipped(check_id, scenario, _RESPONSES_ONLY + "/responses)")
    bad = _violating(responses, field, provider)
    if bad:
        basis = (
            groq_basis
            if provider == "groq"
            else "BharatCode documents this as unsupported (HTTP 400), and the mock answered "
            "as documented."
        )
        return _result(
            check_id,
            scenario,
            CheckStatus.FAILED,
            f"{len(bad)} of {len(responses)} /responses request(s) sent {what} (seq "
            f"{_seqs(bad)}); {basis}",
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
        provider=config.provider,
        groq_basis="Groq lists previous_response_id as not supported on /responses and keeps "
        "no state (contract R3, R4). Whether Groq rejects it is [UNVERIFIED]; the mock answered "
        "HTTP 400, labelled [UNVERIFIED].",
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
    if config.provider == "groq":
        return _groq_budget(requests, scenario)
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
    if config.provider == "groq":
        return _groq_models(requests, scenario)
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


def _groq_budget(requests: Sequence[RecordedExchange], scenario: Scenario) -> CheckResult:
    """FC-RT-006 on Groq: each budget against its model's documented maximum (§1.8)."""
    cid = "FC-RT-006"
    over: list[tuple[RecordedExchange, str, int, str, int]] = []
    odd: list[tuple[RecordedExchange, str, Any]] = []
    unknown: list[tuple[RecordedExchange, str, Any]] = []
    for exchange in requests:
        body = exchange.request_json or {}
        assert exchange.endpoint is not None
        model = body.get("model")
        facts = GROQ_MODELS.get(model) if isinstance(model, str) else None
        for key in _BUDGET_KEYS[exchange.endpoint]:
            if key not in body or body[key] is None:
                continue
            value = body[key]
            if not isinstance(value, int) or isinstance(value, bool):
                odd.append((exchange, key, value))
            elif facts is None or facts.max_completion_tokens is None:
                unknown.append((exchange, key, model))
            elif value > facts.max_completion_tokens:
                assert isinstance(model, str)
                over.append((exchange, key, value, model, facts.max_completion_tokens))
    if over:
        limits = sorted({f"{m} allows {limit}" for _, _, _, m, limit in over})
        return _result(
            cid,
            scenario,
            CheckStatus.FAILED,
            f"{len(over)} request(s) asked for more output tokens than the model's documented "
            f"maximum (seq {_seqs(e for e, *_ in over)}): {'; '.join(limits)} (Groq's "
            f"catalogue of {GROQ_DATE}, contract §1.8). What Groq does with a larger budget "
            "is [UNVERIFIED].",
            expected="≤ the model's maximum completion tokens",
            observed=", ".join(f"{k}={v}" for _, k, v, _, _ in over[:_MAX_EVIDENCE]),
            evidence=[
                _traffic_evidence(e, _short({"model": m, k: v}))
                for e, k, v, m, _ in over[:_MAX_EVIDENCE]
            ],
        )
    if odd:
        return _result(
            cid,
            scenario,
            CheckStatus.NEEDS_REVIEW,
            f"{len(odd)} request(s) sent an output budget that is not an integer (seq "
            f"{_seqs(e for e, _, _ in odd)}).",
            expected="an integer ≤ the model's maximum completion tokens",
            observed=", ".join(f"{k}={_short(v)}" for _, k, v in odd[:_MAX_EVIDENCE]),
            evidence=[_traffic_evidence(e, _short({k: v})) for e, k, v in odd[:_MAX_EVIDENCE]],
        )
    if unknown:
        return _result(
            cid,
            scenario,
            CheckStatus.NEEDS_REVIEW,
            f"{len(unknown)} request(s) set an output budget with a model that is not in "
            f"Groq's catalogue of {GROQ_DATE} (seq {_seqs(e for e, _, _ in unknown)}), so its "
            "maximum is unknown (see FC-RT-007).",
            expected="≤ the model's maximum completion tokens",
            observed=", ".join(sorted({_short(m) for _, _, m in unknown})),
            evidence=[
                _traffic_evidence(e, _short({"model": m})) for e, _, m in unknown[:_MAX_EVIDENCE]
            ],
        )
    return _result(
        cid,
        scenario,
        CheckStatus.PASSED,
        f"No request asked for more than its model's documented maximum completion tokens "
        f"({len(requests)} checked; Groq's catalogue of {GROQ_DATE}).",
        expected="≤ the model's maximum completion tokens",
        observed=f"{len(requests)} request(s) checked",
        evidence=_checked_evidence(requests, "output budget within the model's maximum or unset"),
    )


def _groq_models(requests: Sequence[RecordedExchange], scenario: Scenario) -> CheckResult:
    """FC-RT-007 on Groq: the dated catalogue (§1.8), decommissioned IDs (M2)."""
    cid = "FC-RT-007"

    def model(e: RecordedExchange) -> Any:
        return (e.request_json or {}).get("model")

    missing = [e for e in requests if not isinstance(model(e), str)]
    gone = [e for e in requests if e not in missing and model(e) in GROQ_DECOMMISSIONED_MODELS]
    unknown = [
        e for e in requests if e not in missing and e not in gone and model(e) not in GROQ_MODELS
    ]
    known = [e for e in requests if isinstance(model(e), str) and model(e) in GROQ_MODELS]
    enterprise = [
        e
        for e in known
        if GROQ_MODELS[model(e)].enterprise_only and GROQ_MODELS[model(e)].tier == "production"
    ]
    expected = f"a model in Groq's catalogue of {GROQ_DATE}"
    if missing or gone or unknown:
        bad = by_seq([*missing, *gone, *unknown])
        models = sorted({_short(model(e)) for e in bad})
        parts = []
        if missing:
            parts.append(f"no model in seq {_seqs(missing)} (model is required, contract C1)")
        if gone:
            parts.append(
                f"decommissioned on Groq in seq {_seqs(gone)}: requests to decommissioned IDs "
                "return errors (contract M2)"
            )
        if unknown:
            parts.append(
                f"not in Groq's catalogue of {GROQ_DATE} in seq {_seqs(unknown)} (contract §1.8; "
                "the catalogue is a dated snapshot, GET /models lists the models active for "
                "your key, M6)"
            )
        return _result(
            cid,
            scenario,
            CheckStatus.FAILED,
            f"{len(bad)} request(s) sent a model that is missing or not usable on Groq "
            f"({', '.join(models)}): {'; '.join(parts)}.",
            expected=expected,
            observed=", ".join(models),
            evidence=[_traffic_evidence(e, _short({"model": model(e)})) for e in bad[:10]],
            severity=Severity.HIGH if gone else None,
        )
    if enterprise:
        names = ", ".join(sorted({model(e) for e in enterprise}))
        return _result(
            cid,
            scenario,
            CheckStatus.NEEDS_REVIEW,
            f"{names} is Enterprise-only on Groq (catalogue of {GROQ_DATE}): it was shut down "
            "for free and developer tiers on 16 Aug 2026 (contract M2), so requests on those "
            f"plans fail (seq {_seqs(enterprise)}); check that your plan includes it.",
            expected="a model your plan includes",
            observed=names,
            evidence=[_traffic_evidence(e, model(e)) for e in enterprise[:_MAX_EVIDENCE]],
            severity=Severity.INFO,
        )
    models = sorted({model(e) for e in requests})
    preview = sorted({m for m in models if GROQ_MODELS[m].tier == "preview"})
    note = (
        f" Preview model(s) {', '.join(preview)} are for evaluation only and may be "
        "discontinued at short notice (contract M1)."
        if preview
        else ""
    )
    return _result(
        cid,
        scenario,
        CheckStatus.PASSED,
        f"All {len(requests)} request(s) used a model in Groq's catalogue of {GROQ_DATE}: "
        f"{', '.join(models)}.{note}",
        expected=expected,
        observed=", ".join(models),
        evidence=_checked_evidence(requests, "model in Groq's catalogue"),
    )


# --- FC-RT-009, FC-RT-015: Groq request contract (every scenario on Groq runs) ---------------


@_traffic_check("FC-RT-009")
def check_rt009(
    exchanges: Sequence[RecordedExchange],
    app_run: AppRun | None,
    scenario: Scenario,
    config: CheckConfig,
) -> CheckResult:
    """``store`` on /responses must be false, null or absent (contract R5)."""
    cid = "FC-RT-009"
    responses, _ = split_endpoints(exchanges)
    if not responses:
        return skipped(cid, scenario, _RESPONSES_ONLY + "/responses)")
    bad = [e for e in responses if (e.request_json or {}).get("store") not in (False, None)]
    if bad:
        values = sorted({_short((e.request_json or {}).get("store")) for e in bad})
        return _result(
            cid,
            scenario,
            CheckStatus.FAILED,
            f"{len(bad)} of {len(responses)} /responses request(s) sent store="
            f"{', '.join(values)} (seq {_seqs(bad)}); Groq accepts only store=false or null "
            "(contract R5) and keeps no state. Whether Groq rejects store=true is "
            "[UNVERIFIED]; the mock answers it with HTTP 400, labelled [UNVERIFIED].",
            expected="store false, null or omitted",
            observed=f"store={', '.join(values)} in {len(bad)} request(s)",
            evidence=[
                _traffic_evidence(e, _short({"store": (e.request_json or {}).get("store")}))
                for e in bad[:_MAX_EVIDENCE]
            ],
        )
    return _result(
        cid,
        scenario,
        CheckStatus.PASSED,
        f"None of the {len(responses)} /responses request(s) sent store other than false or "
        "null (omitting it is fine: Groq's default is false, contract R5).",
        expected="store false, null or omitted",
        observed=f"{len(responses)} request(s) checked",
        evidence=_checked_evidence(responses, "store false or omitted"),
    )


def _tool_types(body: dict[str, Any]) -> set[str]:
    tools = body.get("tools")
    if not isinstance(tools, list):
        return set()
    return {t["type"] for t in tools if isinstance(t, dict) and isinstance(t.get("type"), str)}


@_traffic_check("FC-RT-015")
def check_rt015(
    exchanges: Sequence[RecordedExchange],
    app_run: AppRun | None,
    scenario: Scenario,
    config: CheckConfig,
) -> CheckResult:
    """Tool types against what Groq documents (R9, T8): function and mcp tools anywhere;
    browser_search / code_interpreter only on catalogue models with built-in tools."""
    cid = "FC-RT-015"
    responses, chat = split_endpoints(exchanges)
    requests = by_seq([*responses, *chat])
    if not requests:
        return skipped(cid, scenario, "no /responses or /chat/completions request was observed")
    undocumented: list[tuple[RecordedExchange, list[str]]] = []
    wrong_model: list[tuple[RecordedExchange, list[str], str]] = []
    unknown_model: list[tuple[RecordedExchange, list[str], Any]] = []
    for exchange in requests:
        body = exchange.request_json or {}
        types = _tool_types(body)
        extra = sorted(types - GROQ_DOCUMENTED_TOOL_TYPES)
        if extra:
            undocumented.append((exchange, extra))
        builtin = sorted(types & GROQ_BUILTIN_TOOL_TYPES)
        if not builtin:
            continue
        model = body.get("model")
        facts = GROQ_MODELS.get(model) if isinstance(model, str) else None
        if facts is None:
            unknown_model.append((exchange, builtin, model))
        elif not facts.builtin_tools:
            assert isinstance(model, str)
            wrong_model.append((exchange, builtin, model))
    if undocumented or wrong_model:
        parts = []
        evidence = []
        if undocumented:
            names = sorted({t[:80] for _, ts in undocumented for t in ts})
            parts.append(
                f"tool type {', '.join(names)} (seq {_seqs(e for e, _ in undocumented)}) is not "
                "one Groq documents"
            )
            evidence += [
                _traffic_evidence(e, _short({"tool_types": ts}))
                for e, ts in undocumented[:_MAX_EVIDENCE]
            ]
        if wrong_model:
            pairs = sorted({f"{', '.join(ts)} with {m}" for _, ts, m in wrong_model})
            parts.append(
                f"built-in tool {'; '.join(pairs)} (seq {_seqs(e for e, _, _ in wrong_model)}): "
                "Groq documents built-in tools only on openai/gpt-oss-20b and "
                f"openai/gpt-oss-120b (models as listed on {GROQ_DATE})"
            )
            evidence += [
                _traffic_evidence(e, _short({"model": m, "tool_types": ts}))
                for e, ts, m in wrong_model[:_MAX_EVIDENCE]
            ]
        return _result(
            cid,
            scenario,
            CheckStatus.FAILED,
            f"{'; '.join(parts)}. Groq documents function tools, the built-in browser_search "
            "and code_interpreter, and remote mcp tools (contract R9, T8); what Groq answers "
            "for other tools is [UNVERIFIED].",
            expected="function/mcp tools, built-in tools only on GPT-OSS models",
            observed="; ".join(parts),
            evidence=evidence,
        )
    if unknown_model:
        models = sorted({_short(m) for _, _, m in unknown_model})
        return _result(
            cid,
            scenario,
            CheckStatus.NEEDS_REVIEW,
            f"Built-in tools were sent with model(s) {', '.join(models)} (seq "
            f"{_seqs(e for e, _, _ in unknown_model)}), which are not in Groq's catalogue of "
            f"{GROQ_DATE}; whether they support built-in tools is unknown (see FC-RT-007).",
            expected="built-in tools only on GPT-OSS models",
            observed=", ".join(models),
            evidence=[
                _traffic_evidence(e, _short({"model": m, "tool_types": ts}))
                for e, ts, m in unknown_model[:_MAX_EVIDENCE]
            ],
        )
    with_tools = [e for e in requests if _tool_types(e.request_json or {})]
    return _result(
        cid,
        scenario,
        CheckStatus.PASSED,
        f"Every tool sent is one the request's model supports ({len(requests)} request(s) "
        f"checked, {len(with_tools)} with tools).",
        expected="function/mcp tools, built-in tools only on GPT-OSS models",
        observed=f"{len(with_tools)} request(s) with tools",
        evidence=_checked_evidence(requests, "supported tool types"),
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
        if config.provider == "groq":
            basis = (
                "Groq requires a tool message's tool_call_id to match the call's id (contract T5)"
                if rnd.tool.endpoint is Endpoint.CHAT_COMPLETIONS
                else "a function_call_output pairs with its call only by call_id (OpenAI "
                "convention, [UNVERIFIED] for Groq: contract T10)"
            )
        else:
            basis = "BharatCode pairs a result with its call only by call_id"
        message = (
            f"The tool result's call_id ({', '.join(seen)}) does not match the issued call_id "
            f"({', '.join(issued)}); {basis}."
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
    if config.provider == "groq":
        return _groq_tool_output(cid, scenario, results, wrong, chat_parts)
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


def _groq_tool_output(
    cid: str,
    scenario: Scenario,
    results: Sequence[tuple[RecordedExchange, ToolResult]],
    wrong: Sequence[tuple[RecordedExchange, ToolResult]],
    chat_parts: Sequence[tuple[RecordedExchange, ToolResult]],
) -> CheckResult:
    """FC-RT-013 on Groq. Chat: T5 documents a string, so another type is ``failed``; a list
    of content parts may still be accepted ([UNVERIFIED]) → ``needs_review``. Responses: the
    function_call_output format is the OpenAI convention, [UNVERIFIED] for Groq (T10), so a
    non-string output is ``needs_review``."""
    chat_wrong = [(f, r) for f, r in wrong if r.style == "tool_message"]
    unverified = [*chat_parts, *[(f, r) for f, r in wrong if r.style != "tool_message"]]
    if chat_wrong:
        kinds = sorted({type(r.output).__name__ for _, r in chat_wrong})
        return _result(
            cid,
            scenario,
            CheckStatus.FAILED,
            f"{len(chat_wrong)} Chat tool message(s) sent a {'/'.join(kinds)} instead of a "
            f"string (seq {_seqs(f for f, _ in chat_wrong)}); Groq documents tool message "
            "content as a string and recommends json.dumps for structured data (contract T5).",
            expected="a JSON string",
            observed=", ".join(kinds),
            evidence=[
                _traffic_evidence(f, _short({"tool_call_id": r.call_id, "content": r.output}))
                for f, r in chat_wrong[:_MAX_EVIDENCE]
            ],
        )
    if unverified:
        kinds = sorted({type(r.output).__name__ for _, r in unverified})
        return _result(
            cid,
            scenario,
            CheckStatus.NEEDS_REVIEW,
            f"{len(unverified)} tool result(s) sent a {'/'.join(kinds)} instead of a string "
            f"(seq {_seqs(f for f, _ in unverified)}). Groq documents string content for Chat "
            "tool messages (contract T5); whether a list of parts, or a non-string "
            "function_call_output on /responses (T10), is accepted is [UNVERIFIED].",
            expected="a JSON string",
            observed=", ".join(kinds),
            evidence=[
                _traffic_evidence(f, _short({"call_id": r.call_id}))
                for f, r in unverified[:_MAX_EVIDENCE]
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
        if config.provider != "groq":
            unverified = (
                " (Chat: OpenAI-compatible convention; BharatCode's behaviour is [UNVERIFIED])"
            )
    if config.provider == "groq":
        if cont.endpoint is Endpoint.RESPONSES:
            # R3 (replay history) is verified; the item format is the OpenAI convention (T10).
            unverified = " (Responses item format: OpenAI convention, [UNVERIFIED] for Groq: T10)"
            stateless = "Groq's Responses API is not stateful (contract R3)"
        else:
            stateless = "Groq's Chat tool round trip carries them in the messages (contract T4, T5)"
    else:
        stateless = "BharatCode keeps no state"
    evidence = [_traffic_evidence(rnd.tool, f"issued call_ids={', '.join(issued)}")]
    if problems:
        return _result(
            cid,
            scenario,
            CheckStatus.FAILED,
            f"The continuation (seq {cont.seq}) does not replay {' and '.join(problems)}; "
            f"{stateless}, so the follow-up must carry them{unverified}.",
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
    # The attempts end at the first answer that is not a 503: a request sent after the app got
    # its answer (a follow-up, a second question) is not a retry of the busy error.
    attempts: list[RecordedExchange] = []
    for exchange in (e for e in model if e.seq >= busy[0].seq):
        attempts.append(exchange)
        if exchange.response_status != 503:
            break
    return attempts, None


def _sdk_note(retries: Sequence[RecordedExchange], provider: RuntimeProvider = "bharatcode") -> str:
    counts = [sdk_retry_count(e) for e in retries]
    if counts and all(c is not None and c > 0 for c in counts):
        shown = ", ".join(str(c) for c in counts)
        if provider == "groq":
            return (
                f" The retries carry x-stainless-retry-count ({shown}), which the OpenAI and "
                "groq SDKs send (contract K6): the SDK's built-in retries (2 by default in "
                "both, K5) did the retrying, which may or may not be deliberate."
            )
        return (
            " The retries carry the OpenAI SDK's x-stainless-retry-count header "
            f"({shown}): the SDK's built-in retries "
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
        what = (
            "the 503 was not retried (Groq's errors page says to wait before retrying a 503, "
            "contract E3)"
            if config.provider == "groq"
            else "the documented busy 503 was not retried"
        )
        return _result(
            cid,
            scenario,
            CheckStatus.FAILED,
            f"Only one request was observed before the app stopped ({_exit_text(app_run)}): "
            f"{what}.",
            expected="≥ 2 attempts",
            observed="1 attempt",
            evidence=[_traffic_evidence(attempts[0], "503 busy, no retry")],
        )
    last = attempts[-1]
    return _result(
        cid,
        scenario,
        CheckStatus.PASSED,
        f"{len(attempts)} attempts after the {'' if config.provider == 'groq' else 'busy '}503 "
        f"(seq {_seqs(attempts)}); the last got HTTP {last.response_status} "
        f"({_exit_text(app_run)}).{_sdk_note(attempts[1:], config.provider)}",
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
    note = _sdk_note(attempts[1:], config.provider)
    advice = (
        "Groq recommends exponential backoff with jitter for 429 and 5xx errors (contract E9), "
        "so check that this backoff is what you want"
        if config.provider == "groq"
        else "BharatCode asks clients to wait a few seconds before retrying a 503, so consider "
        "a longer backoff"
    )
    if note:
        sdk_backoff = (
            "The OpenAI SDK's backoff starts at 375–500 ms (the groq SDK's at 0.5 s, K5)"
            if config.provider == "groq"
            else "Its backoff starts at 375–500 ms"
        )
        return _result(
            cid,
            scenario,
            CheckStatus.NEEDS_REVIEW,
            f"Gaps between attempts: {shown}.{note} {sdk_backoff}; {advice}.",
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
            + (
                "503 (Groq: wait before retrying, contract E3)."
                if config.provider == "groq"
                else "busy 503."
            ),
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


# --- FC-RT-020/022/023/030: truncation and stream failure (S03, S05, S06) --------------------


def _fault_exchange(
    exchanges: Sequence[RecordedExchange], step_ids: Sequence[str]
) -> RecordedExchange | None:
    """The first model request the scenario's fault step answered."""
    return next((e for e in model_exchanges(exchanges) if e.step_id in step_ids), None)


def _not_served(
    check_id: str, exchanges: Sequence[RecordedExchange], scenario: Scenario, what: str
) -> CheckResult:
    model = model_exchanges(exchanges)
    if not model:
        return skipped(
            check_id, scenario, "no /responses or /chat/completions request was observed"
        )
    first = model[0]
    return skipped(
        check_id,
        scenario,
        f"the mock's {what} step was never served (the first model request got HTTP "
        f"{first.response_status} from '{first.step_id}')",
    )


def _truncation(
    check_id: str,
    exchanges: Sequence[RecordedExchange],
    scenario: Scenario,
    step_ids: Sequence[str],
) -> tuple[RecordedExchange | None, CheckResult | None]:
    """The truncated answer the app received, or the skipped result when there is none."""
    fault = _fault_exchange(exchanges, step_ids)
    if fault is None:
        return None, _not_served(check_id, exchanges, scenario, "truncation")
    finish = fault.response_summary.get("finish")
    if fault.response_status != 200 or finish not in ("truncated", "truncated_tool_call"):
        return None, skipped(
            check_id,
            scenario,
            f"the truncated answer did not reach the app (HTTP {fault.response_status}, "
            f"finish={_short(finish)})",
        )
    return fault, None


def _tail(text: str) -> str:
    text = text.strip()
    return text if len(text) <= _EXCERPT_CHARS else "…" + text[-(_EXCERPT_CHARS - 1) :]


def _process_evidence(app_run: AppRun) -> list[Evidence]:
    """Exit status plus the end of each non-empty output stream (already redacted)."""
    exit_note = f"{_exit_text(app_run)} after {app_run.duration_ms} ms"
    shown = [
        Evidence(type="process", excerpt=_tail(text), note=f"{name}; {exit_note}")
        for name, text in (("stdout", app_run.stdout_excerpt), ("stderr", app_run.stderr_excerpt))
        if text.strip()
    ]
    return shown or [Evidence(type="process", note=f"{exit_note}; no output")]


def expectation_met(expectation: Expectation, app_run: AppRun) -> bool | None:
    """``True``/``False`` when the finished run shows it; ``None`` when it cannot be judged:
    the app timed out (ADR-030: timing alone never fails a check), or the text was not found
    in an output excerpt FlightCheck had to cut (the rest was not kept)."""
    if app_run.timed_out or app_run.exit_code is None:
        return None
    if expectation.kind is ExpectKind.ANY:
        return True
    if expectation.kind is ExpectKind.NONZERO_EXIT:
        return app_run.exit_code != 0
    stream = (
        app_run.stdout_excerpt
        if expectation.kind is ExpectKind.STDOUT_CONTAINS
        else app_run.stderr_excerpt
    )
    if expectation.text in stream:
        return True
    return None if stream.endswith(TRUNCATION_MARK) else False


def _surfaced(
    check_id: str,
    scenario: Scenario,
    app_run: AppRun | None,
    config: CheckConfig,
    expectation: Expectation | None,
    key: str,
    fault: RecordedExchange,
    fault_text: str,
) -> CheckResult:
    """FC-RT-020/030: judge the process outcome only against an explicit expectation."""
    if app_run is None:
        return skipped(check_id, scenario, "the app did not run")
    fault_ev = _traffic_evidence(fault, note=f"scripted fault ({fault.step_id})")
    evidence = [fault_ev, *_process_evidence(app_run)]
    observed = "timed out" if app_run.timed_out else f"exit {app_run.exit_code}"
    if config.aborted_for_limit:
        return _result(
            check_id,
            scenario,
            CheckStatus.NEEDS_REVIEW,
            f"After {fault_text} (seq {fault.seq}) the app sent more than "
            f"{config.test.max_requests} requests and FlightCheck stopped it, so its handling "
            "cannot be judged (see FC-RT-060).",
            expected=expectation.raw if expectation else None,
            observed="stopped by FlightCheck",
            evidence=evidence,
        )
    if expectation is None:
        return _result(
            check_id,
            scenario,
            CheckStatus.NEEDS_REVIEW,
            f"The mock returned {fault_text} (seq {fault.seq}) and the app finished with "
            f"{_exit_text(app_run)}. No [test.expect] {key} is set, so FlightCheck does not "
            "claim the app surfaced it; review the output below.",
            expected=f"[test.expect] {key} (not set)",
            observed=observed,
            evidence=evidence,
        )
    met = expectation_met(expectation, app_run)
    if met is None:
        why = (
            f"the app did not finish within {config.test.timeout_s} s"
            if app_run.timed_out or app_run.exit_code is None
            else f"{expectation.text!r} was not found in the kept part of the app's output, "
            "which FlightCheck cut at 8 KB"
        )
        return _result(
            check_id,
            scenario,
            CheckStatus.NEEDS_REVIEW,
            f"After {fault_text} (seq {fault.seq}) {why}, so {key} = {expectation.raw!r} "
            "cannot be judged.",
            expected=expectation.raw,
            observed=observed,
            evidence=evidence,
        )
    if met:
        return _result(
            check_id,
            scenario,
            CheckStatus.PASSED,
            f"After {fault_text} (seq {fault.seq}) the app met {key} = {expectation.raw!r} "
            f"({_exit_text(app_run)}).",
            expected=expectation.raw,
            observed=observed,
            evidence=evidence,
        )
    return _result(
        check_id,
        scenario,
        CheckStatus.FAILED,
        f"After {fault_text} (seq {fault.seq}) the app did not meet {key} = "
        f"{expectation.raw!r} ({_exit_text(app_run)}).",
        expected=expectation.raw,
        observed=observed,
        evidence=evidence,
    )


@_traffic_check("FC-RT-020")
def check_rt020(
    exchanges: Sequence[RecordedExchange],
    app_run: AppRun | None,
    scenario: Scenario,
    config: CheckConfig,
) -> CheckResult:
    cid = "FC-RT-020"
    fault, blocked = _truncation(cid, exchanges, scenario, (TRUNCATE_STEP_ID,))
    if blocked is not None:
        return blocked
    assert fault is not None
    text = 'a truncated answer (Responses status "incomplete" / Chat finish_reason "length")'
    return _surfaced(
        cid,
        scenario,
        app_run,
        config,
        config.expectations.on_truncation,
        "on_truncation",
        fault,
        text,
    )


@_traffic_check("FC-RT-022")
def check_rt022(
    exchanges: Sequence[RecordedExchange],
    app_run: AppRun | None,
    scenario: Scenario,
    config: CheckConfig,
) -> CheckResult:
    """A later request sends a tool result for a call_id issued in the truncated response."""
    cid = "FC-RT-022"
    fault = _fault_exchange(exchanges, (TRUNCATED_TOOL_STEP_ID,))
    if fault is None:
        return _not_served(cid, exchanges, scenario, "truncated tool-call")
    if fault.response_summary.get("no_tools"):
        return _unsupported(
            cid,
            scenario,
            "app defines no tools: the request carried no function tool, so the mock "
            "truncated a text answer instead",
        )
    issued = issued_call_ids(fault)
    if fault.response_summary.get("finish") != "truncated_tool_call" or not issued:
        return skipped(
            cid,
            scenario,
            "the truncated tool call did not reach the app (HTTP "
            f"{fault.response_status}, finish={_short(fault.response_summary.get('finish'))})",
        )
    shown = ", ".join(issued)
    fault_ev = _traffic_evidence(fault, f"truncated call_ids={shown}")
    later = [e for e in model_exchanges(exchanges) if e.seq > fault.seq]
    groq_note = (
        " (FlightCheck recommendation. Whether Groq ever returns a cut-off tool call, or a 400 "
        "with failed_generation instead, is [UNVERIFIED]: contract T6.)"
        if config.provider == "groq"
        else ""
    )
    executed = [
        (e, r)
        for e in later
        for r in tool_results(e)
        if isinstance(r.call_id, str) and r.call_id in issued
    ]
    if executed:
        seqs = ", ".join(str(n) for n in dict.fromkeys(e.seq for e, _ in executed))
        return _result(
            cid,
            scenario,
            CheckStatus.FAILED,
            f"The app sent a tool result (seq {seqs}) for {shown}, a function call cut off "
            f"inside a truncated response (seq {fault.seq}). Its arguments were incomplete, so "
            "that result was produced from partial input; the call should not have been "
            f"executed.{groq_note}",
            expected=f"no tool result for {shown}",
            observed=f"tool result for {_short(sorted({r.call_id for _, r in executed}))}",
            evidence=[
                fault_ev,
                *[
                    _traffic_evidence(e, f"tool result call_id={_short(r.call_id)}")
                    for e, r in executed[:_MAX_EVIDENCE]
                ],
            ],
        )
    note = "no tool result for the truncated call"
    return _result(
        cid,
        scenario,
        CheckStatus.PASSED,
        f"No later request carries a tool result for {shown} from the truncated response "
        f"(seq {fault.seq}; {len(later)} later request(s) checked). FlightCheck sees only "
        "traffic: whether the app ran the tool locally without sending a result is not "
        f"observable.{groq_note}",
        expected=f"no tool result for {shown}",
        observed=f"{len(later)} later request(s), none with a result for {shown}",
        evidence=[fault_ev, *_checked_evidence(later, note)],
    )


@_traffic_check("FC-RT-023")
def check_rt023(
    exchanges: Sequence[RecordedExchange],
    app_run: AppRun | None,
    scenario: Scenario,
    config: CheckConfig,
) -> CheckResult:
    """A later request has the same request_sha256 as the truncated one."""
    cid = "FC-RT-023"
    fault, blocked = _truncation(cid, exchanges, scenario, TRUNCATION_STEP_IDS)
    if blocked is not None:
        return blocked
    assert fault is not None
    later = [e for e in model_exchanges(exchanges) if e.seq > fault.seq]
    same = [e for e in later if e.request_sha256 == fault.request_sha256]
    fault_ev = _traffic_evidence(fault, f"truncated; request_sha256={fault.request_sha256[:12]}")
    if same:
        return _result(
            cid,
            scenario,
            CheckStatus.FAILED,
            f"After the truncated answer (seq {fault.seq}) the app resent the identical request "
            f"(seq {_seqs(same)}); the same output budget runs out the same way.",
            expected="no identical request after the truncation",
            observed=f"{len(same)} identical request(s)",
            evidence=[
                fault_ev,
                *[
                    _traffic_evidence(e, f"identical body, request_sha256={e.request_sha256[:12]}")
                    for e in same[:_MAX_EVIDENCE]
                ],
            ],
        )
    return _result(
        cid,
        scenario,
        CheckStatus.PASSED,
        f"No later request repeats the truncated request (seq {fault.seq}; {len(later)} later "
        "request(s) checked).",
        expected="no identical request after the truncation",
        observed=f"{len(later)} later request(s), none identical",
        evidence=[fault_ev, *_checked_evidence(later, "different request body")],
    )


@_traffic_check("FC-RT-030")
def check_rt030(
    exchanges: Sequence[RecordedExchange],
    app_run: AppRun | None,
    scenario: Scenario,
    config: CheckConfig,
) -> CheckResult:
    cid = "FC-RT-030"
    fault = _fault_exchange(exchanges, (STREAM_FAIL_STEP_ID,))
    if fault is None:
        return _not_served(cid, exchanges, scenario, "stream-failure")
    summary = fault.response_summary
    if fault.stream and summary.get("finish") == "client_disconnected":
        text = "a stream the app disconnected from before the scripted failure"
    elif fault.stream and summary.get("finish") == "x_groq_error":
        deltas = summary.get("deltas")
        count = f" after {deltas} text delta(s)" if isinstance(deltas, int) else ""
        text = (
            f"a Chat stream that stopped{count} with a chunk carrying x_groq.error (Groq's "
            "mid-stream error field, contract ST3; the wire format is [UNVERIFIED])"
        )
    elif fault.stream and config.provider == "groq":
        deltas = summary.get("deltas")
        count = f" after {deltas} text delta(s)" if isinstance(deltas, int) else ""
        text = (
            f"a stream that failed{count} (response.failed: OpenAI's event, [UNVERIFIED] for "
            "Groq: contract R15)"
        )
    elif fault.stream:
        deltas = summary.get("deltas")
        count = f" after {deltas} text delta(s)" if isinstance(deltas, int) else ""
        text = f"a stream that failed{count} (response.failed)"
    else:
        text = f"HTTP {fault.response_status} (non-stream request; S06 answers it with a 500)"
    result = _surfaced(
        cid,
        scenario,
        app_run,
        config,
        config.expectations.on_stream_failure,
        "on_stream_failure",
        fault,
        text,
    )
    retries = [e for e in model_exchanges(exchanges) if e.seq > fault.seq]
    if not retries or result.status is CheckStatus.SKIPPED:
        return result
    reminder = (
        f" A later request (seq {_seqs(retries)}) followed the failure, so the app (or its SDK) "
        "retried.{sdk} Discard any partial output from the failed attempt before using the "
        "retry's answer."
    ).format(sdk=_sdk_note(retries, config.provider))
    update: dict[str, Any] = {
        "message": result.message + reminder,
        "evidence": [*result.evidence, *_checked_evidence(retries, "retry after the failure")],
    }
    if result.status is not CheckStatus.FAILED:
        update["severity"] = Severity.INFO  # §17.4: a retry is reported as info
    return result.model_copy(update=update)


# --- FC-RT-044: 429 with retry-after (S11, Groq) -------------------------------------------


def _retry_after_s(exchange: RecordedExchange) -> float | None:
    value = exchange.response_summary.get("retry_after")
    try:
        seconds = float(value) if isinstance(value, str | int | float) else None
    except ValueError:
        return None
    return seconds if seconds is not None and seconds >= 0 else None


@_traffic_check("FC-RT-044")
def check_rt044(
    exchanges: Sequence[RecordedExchange],
    app_run: AppRun | None,
    scenario: Scenario,
    config: CheckConfig,
) -> CheckResult:
    """The next model request after S11's 429 comes at least ``retry-after`` seconds later
    (contract E5, K6). The gap runs from when the mock answered the 429 (``at_ms``) to when the
    next request arrived (``received_ms``, recorded on Groq routes), so it never counts the
    next request's own answer time and never overstates the app's wait."""
    cid = "FC-RT-044"
    fault = _fault_exchange(exchanges, (RATE_LIMIT_STEP_ID,))
    if fault is None:
        return _not_served(cid, exchanges, scenario, "rate-limit (429)")
    wait_s = _retry_after_s(fault)
    if fault.response_status != 429 or wait_s is None:
        return skipped(
            cid,
            scenario,
            f"the 429 with retry-after did not reach the app (HTTP {fault.response_status})",
        )
    need_ms = int(wait_s * 1000)
    fault_ev = _traffic_evidence(fault, f"429, retry-after: {_short(wait_s)} s", note="the 429")
    later = [e for e in model_exchanges(exchanges) if e.seq > fault.seq]
    expected = f"next request ≥ {need_ms} ms after the 429"
    if not later:
        return _result(
            cid,
            scenario,
            CheckStatus.NEEDS_REVIEW,
            f"The mock answered 429 with retry-after: {_short(wait_s)} (seq {fault.seq}) and the "
            f"app sent no further request ({_exit_text(app_run)}). That is right only if the app "
            "does not intend to retry; Groq recommends retrying 429s with backoff (contract E9).",
            expected=expected,
            observed="no later request",
            evidence=[fault_ev, *([] if app_run is None else _process_evidence(app_run))],
        )
    nxt = later[0]
    arrived = nxt.response_summary.get("received_ms")
    if not isinstance(arrived, int) or isinstance(arrived, bool):
        return _result(
            cid,
            scenario,
            CheckStatus.NEEDS_REVIEW,
            f"The next request (seq {nxt.seq}) has no recorded arrival time, so the wait after "
            "the 429 cannot be measured.",
            expected=expected,
            observed="arrival time not recorded",
            evidence=[fault_ev, _traffic_evidence(nxt)],
        )
    gap = arrived - fault.at_ms
    retry = sdk_retry_count(nxt)
    sdk = (
        f" It carries x-stainless-retry-count {retry}: the SDK's built-in retry (OpenAI and "
        "groq SDKs honour retry-after, contract K6) did the waiting."
        if retry is not None and retry > 0
        else ""
    )
    evidence = [fault_ev, _traffic_evidence(nxt, f"arrived {gap} ms after the 429")]
    if gap >= need_ms:
        return _result(
            cid,
            scenario,
            CheckStatus.PASSED,
            f"The next request (seq {nxt.seq}) arrived {gap} ms after the 429, respecting its "
            f"retry-after of {_short(wait_s)} s (contract E5).{sdk}",
            expected=expected,
            observed=f"{gap} ms",
            evidence=evidence,
        )
    return _result(
        cid,
        scenario,
        CheckStatus.FAILED,
        f"The next request (seq {nxt.seq}) arrived {gap} ms after the 429, before its "
        f"retry-after of {_short(wait_s)} s ran out (contract E5: Groq sends retry-after on "
        f"429; E9: back off).{sdk}",
        expected=expected,
        observed=f"{gap} ms",
        evidence=evidence,
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
    "FC-RT-009": check_rt009,
    "FC-RT-011": check_rt011,
    "FC-RT-012": check_rt012,
    "FC-RT-013": check_rt013,
    "FC-RT-014": check_rt014,
    "FC-RT-015": check_rt015,
    "FC-RT-020": check_rt020,
    "FC-RT-022": check_rt022,
    "FC-RT-023": check_rt023,
    "FC-RT-030": check_rt030,
    "FC-RT-040": check_rt040,
    "FC-RT-041": check_rt041,
    "FC-RT-044": check_rt044,
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
    """Every check of ``scenario`` (its own, then the every-scenario checks of the run's
    provider), in order."""
    ordered = by_seq(exchanges)
    return [
        VALIDATORS[cid](ordered, app_run, scenario, config)
        for cid in scenario.check_ids_for(config.provider)
    ]


def skip_all(
    scenario: Scenario, reason: str, provider: RuntimeProvider = "bharatcode"
) -> list[CheckResult]:
    return [skipped(cid, scenario, reason) for cid in scenario.check_ids_for(provider)]


def error_all(
    scenario: Scenario, message: str, provider: RuntimeProvider = "bharatcode"
) -> list[CheckResult]:
    return [errored(cid, scenario, message) for cid in scenario.check_ids_for(provider)]


_OBSERVED = (CheckStatus.PASSED, CheckStatus.FAILED, CheckStatus.NEEDS_REVIEW)


def report_once(
    runs: Sequence[tuple[Sequence[RecordedExchange], Sequence[CheckResult]]],
) -> list[list[CheckResult]]:
    """Report the every-scenario checks (FC-RT-001…007, 050, 060; on Groq runs FC-RT-002,
    006, 007, 009, 015, 050, 060) once per run (ADR-029, ADR-038).

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
            if cid not in REPORTED_ONCE_CHECK_IDS:
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


def to_finding(check: CheckResult, provider: RuntimeProvider = "bharatcode") -> Finding:
    """Mirror a failed check as ``Finding(kind="runtime")`` (plan §21.2) for CI gating, with
    the run provider's title, remediation and documentation link (:func:`meta_for`)."""
    title, remediation, doc_url = meta_for(check.check_id, provider)
    medium = check.check_id == "FC-RT-014" and "[UNVERIFIED]" in check.message
    return Finding(
        id=Finding.make_id(check.check_id, check_ref=check.id),
        rule_id=check.check_id,
        title=title,
        kind=FindingKind.RUNTIME,
        verification=Verification.RUNTIME_MOCK,
        severity=check.severity,
        confidence=Confidence.MEDIUM if medium else Confidence.HIGH,
        message=f"[{check.scenario_id}] {check.message}",
        location=None,
        evidence=list(check.evidence),
        remediation=remediation,
        doc_url=doc_url,
        provider=provider,
        check_ref=check.id,
    )


__all__ = [
    "CHECKS",
    "NO_TRAFFIC_REASON",
    "VALIDATORS",
    "CheckConfig",
    "CheckMeta",
    "ExpectKind",
    "Expectation",
    "Expectations",
    "ToolResult",
    "by_seq",
    "error_all",
    "evaluate",
    "expectation_met",
    "gaps_ms",
    "issued_call_ids",
    "meta_for",
    "model_exchanges",
    "report_once",
    "sdk_retry_count",
    "skip_all",
    "split_endpoints",
    "to_finding",
    "tool_results",
    "user_texts",
]
