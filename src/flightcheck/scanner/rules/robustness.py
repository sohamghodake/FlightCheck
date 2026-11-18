"""Robustness rules FC-GEN-001…007 (plan §16.3).

These run on every call site whatever its provider (``providers={"*"}``). The truncation and
stream heuristics (001/002/003) and FC-GEN-006 are lexical: they look at the attributes and
strings of the enclosing function (or, for module-level code, of the module's top-level code),
so a check made in a helper function is a false positive. They carry at most ``medium``
confidence and say "not observed in <function>" (ADR-013).

Phase G3 (ADR-037, docs/GROQ_CONTRACT.md §4.3): meanings and detection are unchanged; on call
sites (FC-GEN-006/007: files) classified ``groq`` the message, remediation and documentation
link cite Groq instead of BharatCode. FC-GEN-005 also checks groq SDK clients, at ``info``.
"""

from __future__ import annotations

from collections.abc import Iterator

from flightcheck.core.models import Confidence, Finding, Severity
from flightcheck.scanner.engine import ALL_PROVIDERS, RuleContext, RuleMeta, rule
from flightcheck.scanner.parser import (
    GROQ_SDK_CONSTRUCTORS,
    OPENAI_SDK_CONSTRUCTORS,
    CallSite,
    ClientBinding,
    FunctionFacts,
    RepoIndex,
)
from flightcheck.scanner.rules.groq import DOC_API, DOC_LOCAL_TOOLS, DOC_SDK

DOC = "https://bharatcode.ai/build"
DOC_GROQ_ERRORS = "https://console.groq.com/docs/errors"
DOC_GROQ_CHECKLIST = "https://console.groq.com/docs/production-readiness/production-ready-checklist"
ANY = frozenset({ALL_PROVIDERS})
TIMEOUT_CONSTRUCTORS = frozenset(
    {
        "openai.OpenAI",
        "openai.AsyncOpenAI",
        "groq.Groq",
        "groq.AsyncGroq",
        "httpx2.Client",
        "httpx2.AsyncClient",
        "httpx.Client",
        "httpx.AsyncClient",
    }
)
JSON_ERROR_HANDLERS = frozenset({"JSONDecodeError", "ValueError", "Exception", "BaseException"})
TERMINAL_EVENTS = ("response.completed", "response.incomplete", "response.failed")


def _meta(
    rule_id: str,
    title: str,
    severity: Severity,
    confidence: Confidence,
    fix: str,
    doc: str | None = DOC,
) -> RuleMeta:
    return RuleMeta(
        id=rule_id,
        title=title,
        category="robustness",
        default_severity=severity,
        default_confidence=confidence,
        providers=ANY,
        doc_source=doc,
        remediation=fix,
        priority="P0",
    )


def _facts(index: RepoIndex, path: str, function: str | None) -> FunctionFacts | None:
    """Lexical facts of ``function``; module-level code (``None``) uses the module's facts."""
    file = index.file(path)
    if file is None:
        return None
    return file.module_facts if function is None else file.functions.get(function)


def _where(function: str | None) -> str:
    return f"`{function}`" if function else "module-level code"


def _mentions(facts: FunctionFacts, *names: str) -> bool:
    return any(name in facts.attributes or name in facts.strings for name in names)


def _is_groq(ctx: RuleContext, call: CallSite | None) -> bool:
    """The call site is classified ``groq`` (Groq wording, ADR-037)."""
    detection = ctx.provider_of(call) if call is not None else None
    return detection is not None and detection.provider == "groq"


def _file_is_groq(ctx: RuleContext, index: RepoIndex, path: str) -> bool:
    """Every classified call site in ``path`` is ``groq`` (and there is at least one)."""
    file = index.file(path)
    if file is None:
        return False
    providers = {
        detection.provider
        for call in file.call_sites
        if (detection := ctx.provider_of(call)) is not None
    }
    return providers == {"groq"}


# --- FC-GEN-001 -----------------------------------------------------------------------------

GEN001 = _meta(
    "FC-GEN-001",
    "Chat Completions truncation not checked",
    Severity.MEDIUM,
    Confidence.MEDIUM,
    'Check `choice.finish_reason == "length"`: keep the partial output, do not execute tool '
    "calls, and raise max_tokens (≤ 32 000) or shorten the input.",
)
GEN001_GROQ_FIX = (
    'Check `choice.finish_reason == "length"`: keep the partial output, do not execute tool '
    "calls, and raise max_completion_tokens (within the model's maximum on Groq's models page) "
    "or shorten the input."
)


@rule(GEN001)
def chat_truncation(index: RepoIndex, ctx: RuleContext) -> Iterator[Finding]:
    for call in ctx.call_sites_for(GEN001):
        if call.kind != "chat.completions.create" or call.stream is not False:
            continue
        facts = _facts(index, call.location.path, call.function)
        if facts is None or "message" not in facts.attributes:
            continue
        if not ({"content", "tool_calls"} & facts.attributes):
            continue
        if not _mentions(facts, "finish_reason"):
            groq = _is_groq(ctx, call)
            yield ctx.finding(
                GEN001,
                call,
                f"No `finish_reason` check observed in {_where(call.function)}: the reply is "
                'read, but a `finish_reason` of "length" (output cut short, even with HTTP 200) '
                "may go unnoticed."
                + (" `length` is a documented Groq finish_reason (contract C11)." if groq else ""),
                remediation=GEN001_GROQ_FIX if groq else None,
                doc_url=DOC_API if groq else None,
            )


# --- FC-GEN-002 -----------------------------------------------------------------------------

GEN002 = _meta(
    "FC-GEN-002",
    "Responses truncation not checked",
    Severity.MEDIUM,
    Confidence.MEDIUM,
    'Check `resp.status == "incomplete"` (see `resp.incomplete_details.reason`): keep the '
    "partial output, do not execute tool calls, raise max_output_tokens (≤ 32 000).",
)
GEN002_GROQ_FIX = (
    'Check `resp.status == "incomplete"` (see `resp.incomplete_details.reason`): keep the '
    "partial output, do not execute tool calls, raise max_output_tokens (it includes reasoning "
    "tokens) within the model's maximum on Groq's models page."
)


@rule(GEN002)
def responses_truncation(index: RepoIndex, ctx: RuleContext) -> Iterator[Finding]:
    for call in ctx.call_sites_for(GEN002):
        if call.kind != "responses.create" or call.stream is not False:
            continue
        facts = _facts(index, call.location.path, call.function)
        if facts is None or not ({"output_text", "output"} & facts.attributes):
            continue
        if not _mentions(facts, "status", "incomplete_details"):
            groq = _is_groq(ctx, call)
            yield ctx.finding(
                GEN002,
                call,
                f"No `status`/`incomplete_details` check observed in {_where(call.function)}: "
                'the output is read, but a `status` of "incomplete" (cut short, even with '
                "HTTP 200) may go unnoticed."
                + (
                    " Groq documents `incomplete` and `incomplete_details` (contract R10)."
                    if groq
                    else ""
                ),
                remediation=GEN002_GROQ_FIX if groq else None,
                doc_url=DOC_API if groq else None,
            )


# --- FC-GEN-003 -----------------------------------------------------------------------------

GEN003 = _meta(
    "FC-GEN-003",
    "Stream terminal events not handled",
    Severity.MEDIUM,
    Confidence.MEDIUM,
    "Handle `response.completed` (success), `response.incomplete` (truncated: keep the partial "
    "output) and `response.failed` (error) explicitly; for Chat check each chunk's "
    "`finish_reason`. Treat end of stream without a terminal event as a failure.",
)
GEN003_GROQ_FIX = (
    "Chat: check each chunk's `finish_reason` (`length` = truncated) and treat a chunk whose "
    "`x_groq.error` is set as a failure (Groq stopped the stream early); a stream that ends "
    "without a `finish_reason` is a failure. Responses: Groq does not document the stream "
    "events; handle completed, incomplete and failed as in OpenAI's API."
)


@rule(GEN003)
def stream_terminal_events(index: RepoIndex, ctx: RuleContext) -> Iterator[Finding]:
    calls = {call.id: call for call in index.call_sites}
    for file in index.files:
        for loop in file.stream_loops:
            call = calls.get(loop.call_id)
            where = _where(loop.function)
            groq = _is_groq(ctx, call)
            if loop.endpoint == "chat.completions":
                # x_groq.error alone is not accepted: it does not reveal truncation (ADR-037).
                if "finish_reason" in loop.attributes or "finish_reason" in loop.strings:
                    continue
                message = (
                    f"No `finish_reason` check observed in this stream loop in {where}: a "
                    "truncated or interrupted stream may look like a complete answer."
                )
                if groq:
                    message += (
                        " Groq marks a stream it stopped early with `x_groq.error` on a chunk "
                        "(contract ST3)."
                    )
            else:
                facts = _facts(index, file.path, loop.function)
                if facts is None:
                    continue
                reads_delta = "delta" in facts.attributes or _mentions(
                    facts, "response.output_text.delta"
                )
                if not reads_delta or _mentions(facts, *TERMINAL_EVENTS):
                    continue
                message = (
                    f"Stream deltas are read but no terminal event (`response.completed`, "
                    f"`response.incomplete`, `response.failed`) is observed in {where}: a failed "
                    "or truncated stream may look like a complete answer."
                )
                if groq:
                    message += " Groq does not document its Responses stream events (contract R15)."
            yield ctx.finding_at(
                GEN003,
                loop.location,
                message,
                call=call,
                remediation=GEN003_GROQ_FIX if groq else None,
                doc_url=DOC_SDK if groq else None,
            )


# --- FC-GEN-004 -----------------------------------------------------------------------------

GEN004 = _meta(
    "FC-GEN-004",
    "No error handling around the API call",
    Severity.MEDIUM,
    Confidence.MEDIUM,
    "Catch provider errors (e.g. `openai.APIStatusError`): on 503 back off and retry a bounded "
    "number of times; do not retry other 4xx errors; surface the request_id.",
)
GEN004_GROQ_FIX = (
    "Catch the SDK's errors (`groq.APIStatusError`/`groq.APIConnectionError`, or `openai.*` "
    "with the OpenAI SDK): retry 429 after `retry-after` and 500/502/503 with bounded "
    "exponential back-off and jitter; do not retry other 4xx errors; log the request ID "
    "(`x_groq.id` in the response body)."
)


@rule(GEN004)
def no_error_handling(index: RepoIndex, ctx: RuleContext) -> Iterator[Finding]:
    for call in ctx.call_sites_for(GEN004):
        if call.in_try:
            continue
        decorated = bool(call.decorators)
        note = (
            " The function is decorated; a retry decorator may handle errors (not analysed)."
            if decorated
            else ""
        )
        if _is_groq(ctx, call):
            yield ctx.finding(
                GEN004,
                call,
                f"No `try` around this API call observed in {_where(call.function)}: a 429 "
                "(rate limit), 500/502/503, timeout or other 4xx error from Groq may crash the "
                f"app (contract E2, E3, E5).{note}",
                confidence=Confidence.LOW if decorated else None,
                remediation=GEN004_GROQ_FIX,
                doc_url=DOC_GROQ_ERRORS,
            )
            continue
        yield ctx.finding(
            GEN004,
            call,
            f"No `try` around this API call observed in {_where(call.function)}: a 503 (model "
            f"busy), timeout or 4xx error may crash the app.{note}",
            confidence=Confidence.LOW if decorated else None,
        )


# --- FC-GEN-005 -----------------------------------------------------------------------------

GEN005 = _meta(
    "FC-GEN-005",
    "Client constructed without an explicit timeout",
    Severity.LOW,
    Confidence.HIGH,
    "Pass a deliberate timeout, e.g. `OpenAI(..., timeout=httpx2.Timeout(300.0, connect=10.0))` "
    "(values are a recommendation).",
)


GEN005_GROQ_FIX = (
    "Pass a deliberate timeout, e.g. `Groq(..., timeout=httpx.Timeout(60.0, connect=10.0))`, "
    "longer for long generations (values are a recommendation)."
)


def _binding_call(index: RepoIndex, binding_id: str) -> CallSite | None:
    return next((c for c in index.call_sites if c.binding and c.binding.id == binding_id), None)


def _default_timeout(binding: ClientBinding, provider: str | None) -> str:
    """What happens without ``timeout=``, true for the client's own library."""
    if binding.constructor in OPENAI_SDK_CONSTRUCTORS:
        text = "the OpenAI SDK default is 10 minutes [VERIFIED]"
    else:
        text = f"`{binding.constructor.split('.')[0]}` applies its own default timeout"
    if provider == "bharatcode":
        text += " and BharatCode requests may queue [VERIFIED]"
    elif provider == "groq":
        text += " and Groq's production checklist asks apps to configure timeouts (contract ST5)"
    return text


@rule(GEN005)
def client_without_timeout(index: RepoIndex, ctx: RuleContext) -> Iterator[Finding]:
    for file in index.files:
        if file.timeout_overrides:
            continue
        for binding in file.clients:
            if binding.constructor not in TIMEOUT_CONSTRUCTORS or "timeout" in binding.kwargs:
                continue
            detection = ctx.detection.bindings.get(binding.id)
            provider = detection.provider if detection is not None else None
            call = _binding_call(index, binding.id)
            if binding.constructor in GROQ_SDK_CONSTRUCTORS:
                # The groq SDK's own default is bounded (60 s total, 5 s connect, contract K5):
                # a reminder, not a risk (ADR-037).
                finding = ctx.finding_at(
                    GEN005,
                    binding.location,
                    f"`{binding.constructor}` client created without `timeout=` and no "
                    "`.with_options(timeout=...)` in this file: the groq SDK default (60 s "
                    "total, 5 s connect, 2 retries; contract K5) applies. Groq's production "
                    "checklist asks apps to configure timeouts (contract ST5); make sure 60 s "
                    "suits your longest generation.",
                    call=call,
                    severity=Severity.INFO,
                    remediation=GEN005_GROQ_FIX,
                    doc_url=DOC_GROQ_CHECKLIST,
                )
            else:
                finding = ctx.finding_at(
                    GEN005,
                    binding.location,
                    f"`{binding.constructor}` client created without `timeout=` and no "
                    f"`.with_options(timeout=...)` in this file: "
                    f"{_default_timeout(binding, provider)}, so set an explicit, deliberate "
                    "timeout.",
                    call=call,
                    doc_url=DOC_GROQ_CHECKLIST if provider == "groq" else None,
                )
            if detection is not None:
                finding = finding.model_copy(update={"provider": detection.provider})
            yield finding


# --- FC-GEN-006 -----------------------------------------------------------------------------

GEN006 = _meta(
    "FC-GEN-006",
    "Tool arguments parsed without error handling",
    Severity.MEDIUM,
    Confidence.MEDIUM,
    "Parse tool arguments inside `try` (catch `json.JSONDecodeError`), validate them against "
    "the tool schema, and return an error string as the tool output instead of crashing.",
    doc=None,
)
GEN006_GROQ_FIX = (
    "Parse tool arguments (a JSON string, contract T4) inside `try` (catch "
    "`json.JSONDecodeError`), validate them against the tool schema, and return an error string "
    "as the tool output. Groq also rejects invalid tool calls it detects with HTTP 400 and "
    "`error.failed_generation` (contract T6): catch that and retry."
)


@rule(GEN006)
def tool_args_unguarded(index: RepoIndex, ctx: RuleContext) -> Iterator[Finding]:
    for file in index.files:
        for parse in file.argument_parses:
            handled = {name.rsplit(".", 1)[-1] for name in parse.try_handlers}
            if parse.in_try and handled & JSON_ERROR_HANDLERS:
                continue
            groq = _file_is_groq(ctx, index, file.path)
            yield ctx.finding_at(
                GEN006,
                parse.location,
                "`json.loads` of tool-call `arguments` with no `try` catching "
                f"`json.JSONDecodeError`/`ValueError` observed in {_where(parse.function)}: "
                "malformed arguments from the model may crash the tool loop.",
                remediation=GEN006_GROQ_FIX if groq else None,
                doc_url=DOC_LOCAL_TOOLS if groq else None,
            )


# --- FC-GEN-007 -----------------------------------------------------------------------------

GEN007 = _meta(
    "FC-GEN-007",
    "Tool result output is not a string",
    Severity.HIGH,
    Confidence.HIGH,
    'Send a string: `"output": json.dumps(result)` with the matching `call_id` (Chat: '
    '`"content": json.dumps(result)`).',
)


def _string_rule(groq: bool, form: str) -> str:
    """Who requires a string tool output (Groq: contract T5 for Chat, T10 for Responses)."""
    if not groq:
        return "BharatCode requires a string tool output"
    if form == "chat":
        return "Groq documents the tool message `content` as a string (contract T5)"
    return "a string tool output is the OpenAI convention Groq follows (contract T10, [UNVERIFIED])"


@rule(GEN007)
def tool_output_not_string(index: RepoIndex, ctx: RuleContext) -> Iterator[Finding]:
    for file in index.files:
        groq = _file_is_groq(ctx, index, file.path) if file.tool_outputs else False
        doc = DOC_LOCAL_TOOLS if groq else None
        for item in file.tool_outputs:
            field = "output" if item.form == "responses" else "content"
            kind = "`function_call_output` item" if item.form == "responses" else "`tool` message"
            required = _string_rule(groq, item.form)
            if item.value == "literal":
                yield ctx.finding_at(
                    GEN007,
                    item.location,
                    f"The {kind} `{field}` is a non-string literal: {required}, so this request "
                    "may be rejected or misread.",
                    doc_url=doc,
                )
            else:
                yield ctx.finding_at(
                    GEN007,
                    item.location,
                    f"The {kind} `{field}` is a name or call that may not be a string: {required}.",
                    confidence=Confidence.LOW,
                    doc_url=doc,
                )
