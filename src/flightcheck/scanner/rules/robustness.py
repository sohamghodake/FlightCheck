"""Robustness rules FC-GEN-001…007 (plan §16.3).

These run on every call site whatever its provider (``providers={"*"}``). The truncation and
stream heuristics (001/002/003) and FC-GEN-006 are lexical: they look at the attributes and
strings of the enclosing function (or, for module-level code, of the module's top-level code),
so a check made in a helper function is a false positive. They carry at most ``medium``
confidence and say "not observed in <function>" (ADR-013).
"""

from __future__ import annotations

from collections.abc import Iterator

from flightcheck.core.models import Confidence, Finding, Severity
from flightcheck.scanner.engine import ALL_PROVIDERS, RuleContext, RuleMeta, rule
from flightcheck.scanner.parser import CallSite, FunctionFacts, RepoIndex

DOC = "https://bharatcode.ai/build"
ANY = frozenset({ALL_PROVIDERS})
TIMEOUT_CONSTRUCTORS = frozenset(
    {
        "openai.OpenAI",
        "openai.AsyncOpenAI",
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


# --- FC-GEN-001 -----------------------------------------------------------------------------

GEN001 = _meta(
    "FC-GEN-001",
    "Chat Completions truncation not checked",
    Severity.MEDIUM,
    Confidence.MEDIUM,
    'Check `choice.finish_reason == "length"`: keep the partial output, do not execute tool '
    "calls, and raise max_tokens (≤ 32 000) or shorten the input.",
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
            yield ctx.finding(
                GEN001,
                call,
                f"No `finish_reason` check observed in {_where(call.function)}: the reply is "
                'read, but a `finish_reason` of "length" (output cut short, even with HTTP 200) '
                "may go unnoticed.",
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


@rule(GEN002)
def responses_truncation(index: RepoIndex, ctx: RuleContext) -> Iterator[Finding]:
    for call in ctx.call_sites_for(GEN002):
        if call.kind != "responses.create" or call.stream is not False:
            continue
        facts = _facts(index, call.location.path, call.function)
        if facts is None or not ({"output_text", "output"} & facts.attributes):
            continue
        if not _mentions(facts, "status", "incomplete_details"):
            yield ctx.finding(
                GEN002,
                call,
                f"No `status`/`incomplete_details` check observed in {_where(call.function)}: "
                'the output is read, but a `status` of "incomplete" (cut short, even with '
                "HTTP 200) may go unnoticed.",
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


@rule(GEN003)
def stream_terminal_events(index: RepoIndex, ctx: RuleContext) -> Iterator[Finding]:
    calls = {call.id: call for call in index.call_sites}
    for file in index.files:
        for loop in file.stream_loops:
            call = calls.get(loop.call_id)
            where = _where(loop.function)
            if loop.endpoint == "chat.completions":
                if "finish_reason" in loop.attributes or "finish_reason" in loop.strings:
                    continue
                message = (
                    f"No `finish_reason` check observed in this stream loop in {where}: a "
                    "truncated or interrupted stream may look like a complete answer."
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
            yield ctx.finding_at(GEN003, loop.location, message, call=call)


# --- FC-GEN-004 -----------------------------------------------------------------------------

GEN004 = _meta(
    "FC-GEN-004",
    "No error handling around the API call",
    Severity.MEDIUM,
    Confidence.MEDIUM,
    "Catch provider errors (e.g. `openai.APIStatusError`): on 503 back off and retry a bounded "
    "number of times; do not retry other 4xx errors; surface the request_id.",
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


def _binding_call(index: RepoIndex, binding_id: str) -> CallSite | None:
    return next((c for c in index.call_sites if c.binding and c.binding.id == binding_id), None)


@rule(GEN005)
def client_without_timeout(index: RepoIndex, ctx: RuleContext) -> Iterator[Finding]:
    for file in index.files:
        if file.timeout_overrides:
            continue
        for binding in file.clients:
            if binding.constructor not in TIMEOUT_CONSTRUCTORS or "timeout" in binding.kwargs:
                continue
            finding = ctx.finding_at(
                GEN005,
                binding.location,
                f"`{binding.constructor}` client created without `timeout=` and no "
                "`.with_options(timeout=...)` in this file: the OpenAI SDK default is 10 minutes "
                "[VERIFIED] and BharatCode requests may queue [VERIFIED], so set an explicit, "
                "deliberate timeout.",
                call=_binding_call(index, binding.id),
            )
            detection = ctx.detection.bindings.get(binding.id)
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


@rule(GEN006)
def tool_args_unguarded(index: RepoIndex, ctx: RuleContext) -> Iterator[Finding]:
    for file in index.files:
        for parse in file.argument_parses:
            handled = {name.rsplit(".", 1)[-1] for name in parse.try_handlers}
            if parse.in_try and handled & JSON_ERROR_HANDLERS:
                continue
            yield ctx.finding_at(
                GEN006,
                parse.location,
                "`json.loads` of tool-call `arguments` with no `try` catching "
                f"`json.JSONDecodeError`/`ValueError` observed in {_where(parse.function)}: "
                "malformed arguments from the model may crash the tool loop.",
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


@rule(GEN007)
def tool_output_not_string(index: RepoIndex, ctx: RuleContext) -> Iterator[Finding]:
    for file in index.files:
        for item in file.tool_outputs:
            field = "output" if item.form == "responses" else "content"
            kind = "`function_call_output` item" if item.form == "responses" else "`tool` message"
            if item.value == "literal":
                yield ctx.finding_at(
                    GEN007,
                    item.location,
                    f"The {kind} `{field}` is a non-string literal: BharatCode requires a "
                    "string tool output, so this request may be rejected or misread.",
                )
            else:
                yield ctx.finding_at(
                    GEN007,
                    item.location,
                    f"The {kind} `{field}` is a name or call that may not be a string: "
                    "BharatCode requires a string tool output.",
                    confidence=Confidence.LOW,
                )
