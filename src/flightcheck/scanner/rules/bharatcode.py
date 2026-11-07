"""BharatCode contract rules FC-BC-001…008 (plan §16.3).

Every fact comes from docs/BHARATCODE_CONTRACT.md §15.1 (source: https://bharatcode.ai/build,
read 6 Nov 2026). Only literal values are inspected; ``Unknown`` values never produce a
contract violation. FC-BC-009 is P1 and not implemented yet.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from flightcheck.core.config import (
    BHARATCODE_DOCUMENTED_MODELS,
    BHARATCODE_PRO_ONLY_MODEL,
    FILE_INPUT_KEYS,
    MAX_OUTPUT_TOKENS_CAP,
    UNSUPPORTED_INPUT_TYPES,
)
from flightcheck.core.models import Confidence, Finding, FindingKind, Severity
from flightcheck.core.redact import redact
from flightcheck.scanner.ast_utils import Unknown
from flightcheck.scanner.engine import RuleContext, RuleMeta, rule
from flightcheck.scanner.parser import CallSite, RepoIndex

DOC = "https://bharatcode.ai/build"
CATALOGUE_DATE = "6 Nov 2026"
PRO_ONLY_MODEL = BHARATCODE_PRO_ONLY_MODEL
BC = frozenset({"bharatcode"})
FILE_KEYS = FILE_INPUT_KEYS
_MODEL_DISPLAY_LIMIT = 80


def _meta(
    rule_id: str,
    title: str,
    severity: Severity,
    confidence: Confidence,
    remediation: str,
    kind: FindingKind = FindingKind.STATIC,
) -> RuleMeta:
    return RuleMeta(
        id=rule_id,
        title=title,
        category="bharatcode-contract",
        default_severity=severity,
        default_confidence=confidence,
        providers=BC,
        doc_source=DOC,
        remediation=remediation,
        priority="P0",
        kind=kind,
    )


def _is_responses(call: CallSite) -> bool:
    return call.endpoint == "responses"


def _payload_visible(call: CallSite) -> bool:
    """A raw post without ``json=`` sends a body FlightCheck cannot see."""
    return call.kind != "http.post" or "json" in call.kwargs


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


# --- FC-BC-001 ------------------------------------------------------------------------------

BC001 = _meta(
    "FC-BC-001",
    "store=True on /responses",
    Severity.HIGH,
    Confidence.HIGH,
    "Set store=False and replay the conversation history in `input` on each request.",
)


@rule(BC001)
def store_true(index: RepoIndex, ctx: RuleContext) -> Iterator[Finding]:
    for call in ctx.call_sites_for(BC001):
        if _is_responses(call) and call.params.get("store") is True:
            yield ctx.finding(
                BC001,
                call,
                "store=True on /responses: BharatCode documents store:true as unsupported "
                "(HTTP 400); requests from this call site will be rejected.",
            )


# --- FC-BC-002 ------------------------------------------------------------------------------

BC002 = _meta(
    "FC-BC-002",
    "previous_response_id used",
    Severity.HIGH,
    Confidence.HIGH,
    "Remove previous_response_id; send the prior turns and the prior `output` items in `input`.",
)


@rule(BC002)
def previous_response_id(index: RepoIndex, ctx: RuleContext) -> Iterator[Finding]:
    for call in ctx.call_sites_for(BC002):
        if not _is_responses(call) or "previous_response_id" not in call.params:
            continue
        value = call.params["previous_response_id"]
        if value is None:
            continue
        literal = not isinstance(value, Unknown)
        yield ctx.finding(
            BC002,
            call,
            "previous_response_id parameter passed: BharatCode's Responses API is stateless "
            "and documents previous_response_id as unsupported (HTTP 400).",
            confidence=Confidence.HIGH if literal else Confidence.MEDIUM,
        )


# --- FC-BC-003 ------------------------------------------------------------------------------

BC003 = _meta(
    "FC-BC-003",
    "Hosted (non-function) tools",
    Severity.HIGH,
    Confidence.HIGH,
    "Implement the capability as your own function tool; BharatCode does not run hosted tools.",
)


def _hosted_tool_types(tools: Any) -> list[str]:
    if not isinstance(tools, list | tuple):
        return []
    found = {
        tool["type"]
        for tool in tools
        if isinstance(tool, dict) and isinstance(tool.get("type"), str)
        if tool["type"] != "function"
    }
    return sorted(found)


@rule(BC003)
def hosted_tools(index: RepoIndex, ctx: RuleContext) -> Iterator[Finding]:
    for call in ctx.call_sites_for(BC003):
        types = _hosted_tool_types(call.params.get("tools"))
        confidence = Confidence.HIGH
        if not types:
            types = _hosted_tool_types(call.one_hop.get("tools"))
            confidence = Confidence.MEDIUM
        if not types:
            continue
        names = ", ".join(repr(t[:_MODEL_DISPLAY_LIMIT]) for t in types)
        yield ctx.finding(
            BC003,
            call,
            f"Hosted tool type {names} in `tools`: BharatCode supports only your own function "
            "tools and documents hosted tools as unsupported (HTTP 400).",
            confidence=confidence,
        )


# --- FC-BC-004 ------------------------------------------------------------------------------

BC004 = _meta(
    "FC-BC-004",
    "background=True on /responses",
    Severity.HIGH,
    Confidence.HIGH,
    "Run the request synchronously, or in your own worker queue.",
)


@rule(BC004)
def background_true(index: RepoIndex, ctx: RuleContext) -> Iterator[Finding]:
    for call in ctx.call_sites_for(BC004):
        if _is_responses(call) and call.params.get("background") is True:
            yield ctx.finding(
                BC004,
                call,
                "background=True on /responses: BharatCode documents background jobs as "
                "unsupported (HTTP 400); requests from this call site will be rejected.",
            )


# --- FC-BC-005 ------------------------------------------------------------------------------

BC005 = _meta(
    "FC-BC-005",
    "Output budget above 32 000",
    Severity.MEDIUM,
    Confidence.HIGH,
    "Use an output budget of at most 32 000 tokens; budgets include reasoning. For short "
    'answers, use reasoning={"effort": "none"} (Responses) or reasoning_effort="none" (Chat).',
)


@rule(BC005)
def output_budget(index: RepoIndex, ctx: RuleContext) -> Iterator[Finding]:
    for call in ctx.call_sites_for(BC005):
        names = (
            ("max_output_tokens",)
            if _is_responses(call)
            else ("max_tokens", "max_completion_tokens")
        )
        for name in names:
            value = call.params.get(name)
            if _is_int(value) and value > MAX_OUTPUT_TOKENS_CAP:
                yield ctx.finding(
                    BC005,
                    call,
                    f"{name}={value} exceeds the 32,000-token output budget BharatCode "
                    "documents; the budget includes reasoning.",
                )


# --- FC-BC-006 ------------------------------------------------------------------------------

BC006 = _meta(
    "FC-BC-006",
    "Model not in BharatCode's documented catalogue",
    Severity.MEDIUM,
    Confidence.MEDIUM,
    "Use qwen-3.8-27b, or a model returned by GET /models for your key.",
)


@rule(BC006)
def model_catalogue(index: RepoIndex, ctx: RuleContext) -> Iterator[Finding]:
    for call in ctx.call_sites_for(BC006):
        model = call.params.get("model")
        if not isinstance(model, str):
            continue
        shown = redact(model[:_MODEL_DISPLAY_LIMIT])
        if model == PRO_ONLY_MODEL:
            yield ctx.finding(
                BC006,
                call,
                f"model {shown!r} requires a Pro plan on BharatCode; requests with other "
                "plans may be refused.",
                severity=Severity.INFO,
                confidence=Confidence.HIGH,
            )
        elif model not in BHARATCODE_DOCUMENTED_MODELS:
            yield ctx.finding(
                BC006,
                call,
                f"model {shown!r} is not in the catalogue documented on {CATALOGUE_DATE}; "
                "GET /models lists models eligible for your key.",
            )


# --- FC-BC-007 ------------------------------------------------------------------------------

BC007 = _meta(
    "FC-BC-007",
    "store not set to False on /responses",
    Severity.LOW,
    Confidence.MEDIUM,
    "Pass store=False explicitly on every /responses request.",
    kind=FindingKind.HYPOTHESIS,
)


@rule(BC007)
def store_omitted(index: RepoIndex, ctx: RuleContext) -> Iterator[Finding]:
    for call in ctx.call_sites_for(BC007):
        if not _is_responses(call) or not _payload_visible(call):
            continue
        if "store" in call.params or call.has_star_kwargs:
            continue
        yield ctx.finding(
            BC007,
            call,
            "store not set: BharatCode instructs store=false on /responses; what happens when "
            "it is omitted is [UNVERIFIED].",
        )


# --- FC-BC-008 ------------------------------------------------------------------------------

BC008 = _meta(
    "FC-BC-008",
    "Unsupported input modality",
    Severity.HIGH,
    Confidence.MEDIUM,
    "Extract the text (or use image input, which is supported) before calling the API.",
)


def _unsupported_items(value: Any, depth: int = 0) -> list[str]:
    """Content types/keys BharatCode documents as unsupported (audio, video or file input)."""
    if depth > 20:
        return []
    found: list[str] = []
    if isinstance(value, dict):
        item_type = value.get("type")
        if isinstance(item_type, str) and item_type in UNSUPPORTED_INPUT_TYPES:
            found.append(item_type)
        found.extend(key for key in FILE_KEYS if key in value)
        for child in value.values():
            found.extend(_unsupported_items(child, depth + 1))
    elif isinstance(value, list | tuple):
        for child in value:
            found.extend(_unsupported_items(child, depth + 1))
    return found


@rule(BC008)
def input_modality(index: RepoIndex, ctx: RuleContext) -> Iterator[Finding]:
    for call in ctx.call_sites_for(BC008):
        if not _is_responses(call):
            continue
        found = _unsupported_items(call.params.get("input"))
        if not found:
            found = _unsupported_items(call.one_hop.get("input"))
        if not found:
            continue
        names = ", ".join(sorted(set(found)))
        yield ctx.finding(
            BC008,
            call,
            f"Input contains {names}: BharatCode documents audio, video and file input as "
            "unsupported (HTTP 400); the exact rejected type names are [UNVERIFIED].",
        )
