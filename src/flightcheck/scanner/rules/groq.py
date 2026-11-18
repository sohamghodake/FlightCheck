"""Groq contract rules FC-GQ-001…019 (docs/GROQ_CONTRACT.md §4.4, phase G3, ADR-037).

Every rule cites a row of docs/GROQ_CONTRACT.md (read 18 Nov 2026) in its comment and message.
Only literal values are inspected (``tools`` also one hop, as FC-BC-003); ``Unknown`` values
never produce a contract violation, except that a parameter Groq documents as unsupported is
flagged at lower confidence when its value is not a literal (the FC-BC-002 precedent). An HTTP
status is claimed only where the contract states it (rows C2, C4, M4, S8, T3). Model facts come
from ``core.config.GROQ_MODELS``, a snapshot dated ``GROQ_CATALOGUE_DATE``.

Gating (ADR-037): these rules run on call sites (FC-GQ-015: client constructors) classified
``groq`` at any confidence, or everywhere with ``--provider groq``. A Groq model ID alone never
makes a call site Groq (GPT-OSS IDs are served elsewhere too); detection decides.

Not implemented: FC-GQ-016 (``name`` on a message; held until G5 because contract rows C2 and
C3 contradict each other) and FC-GQ-020 (``parallel_tool_calls`` on GPT-OSS, P1).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Literal

from flightcheck.core.config import (
    GROQ_CATALOGUE_DATE,
    GROQ_DECOMMISSIONED_MODELS,
    GROQ_HOST,
    GROQ_MODELS,
    GroqModelFacts,
)
from flightcheck.core.models import Confidence, Finding, Severity
from flightcheck.core.redact import redact
from flightcheck.scanner.ast_utils import Unknown
from flightcheck.scanner.engine import RuleContext, RuleMeta, rule
from flightcheck.scanner.parser import (
    GROQ_SDK_CONSTRUCTORS,
    OPENAI_SDK_CONSTRUCTORS,
    CallSite,
    ClientBinding,
    RepoIndex,
)

GROQ = frozenset({"groq"})
DATE = GROQ_CATALOGUE_DATE
_DOCS = "https://console.groq.com/docs"
DOC_API = f"{_DOCS}/api-reference"
DOC_OPENAI = f"{_DOCS}/openai"
DOC_RESPONSES = f"{_DOCS}/responses-api"
DOC_MODELS = f"{_DOCS}/models"
DOC_DEPRECATIONS = f"{_DOCS}/deprecations"
DOC_STRUCTURED = f"{_DOCS}/structured-outputs"
DOC_BUILTIN = f"{_DOCS}/tool-use/built-in-tools"
DOC_REASONING = f"{_DOCS}/reasoning"
DOC_LOCAL_TOOLS = f"{_DOCS}/tool-use/local-tool-calling"
DOC_SDK = "https://github.com/groq/groq-python"
OPENAI_PATH = "/openai/v1"
_DISPLAY_LIMIT = 80

# Tool types Groq documents (contract R9, T8): function tools, the built-in browser_search and
# code_interpreter (GPT-OSS models only), and remote MCP tools.
BUILTIN_TOOL_TYPES = frozenset({"browser_search", "code_interpreter"})
DOCUMENTED_TOOL_TYPES = frozenset({"function", "mcp"}) | BUILTIN_TOOL_TYPES
MAX_STOP_SEQUENCES = 4  # contract C7
MAX_TOOLS = 128  # contract T1
REJECTED_CHAT_PARAMS = ("logprobs", "logit_bias", "top_logprobs")  # contract C2 (400)
UNSUPPORTED_RESPONSES_PARAMS = ("include", "safety_identifier", "prompt_cache_key", "prompt")
NO_EFFECT_PENALTIES = ("frequency_penalty", "presence_penalty")  # contract C5
# Models shut down for free/developer tiers on 16 Aug 2026, kept for Enterprise (contract M2,
# §1.8): the production models marked Enterprise-only in the catalogue.
FREE_TIER_SHUTDOWN_MODELS = frozenset(
    model
    for model, facts in GROQ_MODELS.items()
    if facts.enterprise_only and facts.tier == "production"
)


def _meta(
    rule_id: str,
    title: str,
    severity: Severity,
    confidence: Confidence,
    remediation: str,
    doc: str,
    priority: Literal["P0", "P1", "P2"] = "P0",
) -> RuleMeta:
    return RuleMeta(
        id=rule_id,
        title=title,
        category="groq-contract",
        default_severity=severity,
        default_confidence=confidence,
        providers=GROQ,
        doc_source=doc,
        remediation=remediation,
        priority=priority,
    )


# --- helpers --------------------------------------------------------------------------------


def _is_responses(call: CallSite) -> bool:
    return call.endpoint == "responses"


def _is_chat(call: CallSite) -> bool:
    return call.endpoint == "chat.completions"


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _model(call: CallSite) -> str | None:
    model = call.params.get("model")
    return model if isinstance(model, str) else None


def _facts(call: CallSite) -> tuple[str, GroqModelFacts] | None:
    """``(model, facts)`` for a literal model in the catalogue, else ``None``."""
    model = _model(call)
    if model is None or model not in GROQ_MODELS:
        return None
    return model, GROQ_MODELS[model]


def _shown(text: str) -> str:
    return repr(redact(text[:_DISPLAY_LIMIT]))


def _supplied(call: CallSite, name: str) -> bool:
    """``name`` is passed with a value other than literal ``None``."""
    return name in call.params and call.params[name] is not None


def _tools(call: CallSite) -> tuple[list[Any] | None, Confidence]:
    """The literal ``tools`` list (or the one-hop literal it names) and the confidence to use."""
    tools = call.params.get("tools")
    if isinstance(tools, list | tuple):
        return list(tools), Confidence.HIGH
    hop = call.one_hop.get("tools")
    if isinstance(hop, list | tuple):
        return list(hop), Confidence.MEDIUM
    return None, Confidence.HIGH


def _has_tools(call: CallSite) -> bool:
    tools, _ = _tools(call)
    return bool(tools)


def _structured_format(call: CallSite) -> dict[str, Any] | None:
    """The literal structured-output format: Chat ``response_format``, Responses ``text.format``."""
    if _is_chat(call):
        value = call.params.get("response_format")
        return value if isinstance(value, dict) else None
    text = call.params.get("text")
    if isinstance(text, dict) and isinstance(text.get("format"), dict):
        return text["format"]
    return None


def _json_schema(fmt: dict[str, Any] | None, call: CallSite) -> dict[str, Any] | None:
    """The json_schema settings (``strict``, ``schema``) of a ``json_schema`` format."""
    if fmt is None or fmt.get("type") != "json_schema":
        return None
    if _is_chat(call):
        inner = fmt.get("json_schema")
        return inner if isinstance(inner, dict) else {}
    return fmt  # Responses: name/schema/strict sit in text.format itself (contract R2)


def _streams(call: CallSite) -> bool:
    return call.stream is True


# --- FC-GQ-001 (contract R4, R5) ------------------------------------------------------------

GQ001 = _meta(
    "FC-GQ-001",
    "store=True on /responses",
    Severity.HIGH,
    Confidence.HIGH,
    "Remove store=True (Groq's default is store=false) and replay the conversation history in "
    "`input` on each request.",
    DOC_API,
)


@rule(GQ001)
def store_true(index: RepoIndex, ctx: RuleContext) -> Iterator[Finding]:
    for call in ctx.call_sites_for(GQ001):
        if _is_responses(call) and call.params.get("store") is True:
            yield ctx.finding(
                GQ001,
                call,
                "store=True on /responses: Groq documents `store` as not supported yet and "
                "accepting only false or null (contract R4, R5); what happens when true is sent "
                "is [UNVERIFIED].",
            )


# --- FC-GQ-002 (contract R4, R6) ------------------------------------------------------------

GQ002 = _meta(
    "FC-GQ-002",
    "previous_response_id used",
    Severity.HIGH,
    Confidence.HIGH,
    "Remove previous_response_id; send the prior turns and the prior `output` items in `input` "
    "on every request.",
    DOC_RESPONSES,
)


@rule(GQ002)
def previous_response_id(index: RepoIndex, ctx: RuleContext) -> Iterator[Finding]:
    for call in ctx.call_sites_for(GQ002):
        if not _is_responses(call) or not _supplied(call, "previous_response_id"):
            continue
        literal = not isinstance(call.params["previous_response_id"], Unknown)
        yield ctx.finding(
            GQ002,
            call,
            "previous_response_id passed: Groq's Responses API is not stateful; it documents "
            "previous_response_id as not supported and always null in responses (contract R4, "
            "R6), so the request may be rejected or the earlier turns silently ignored "
            "([UNVERIFIED] which).",
            confidence=Confidence.HIGH if literal else Confidence.MEDIUM,
        )


# --- FC-GQ-003 (contract R9, T8) ------------------------------------------------------------

GQ003 = _meta(
    "FC-GQ-003",
    "Unsupported hosted or built-in tool",
    Severity.HIGH,
    Confidence.HIGH,
    "Use function tools you run yourself; Groq's built-in browser_search and code_interpreter "
    "need openai/gpt-oss-20b or openai/gpt-oss-120b.",
    DOC_BUILTIN,
)


def _tool_types(tools: list[Any]) -> set[str]:
    return {
        tool["type"]
        for tool in tools
        if isinstance(tool, dict) and isinstance(tool.get("type"), str)
    }


@rule(GQ003)
def hosted_tools(index: RepoIndex, ctx: RuleContext) -> Iterator[Finding]:
    for call in ctx.call_sites_for(GQ003):
        tools, confidence = _tools(call)
        if not tools:
            continue
        types = _tool_types(tools)
        undocumented = sorted(types - DOCUMENTED_TOOL_TYPES)
        if undocumented:
            names = ", ".join(_shown(t) for t in undocumented)
            yield ctx.finding(
                GQ003,
                call,
                f"Tool type {names} in `tools`: Groq documents only function tools, the "
                "built-in browser_search and code_interpreter, and remote mcp tools (contract "
                "R9, T8); this hosted tool type is not documented as supported.",
                confidence=confidence,
            )
        builtin = sorted(types & BUILTIN_TOOL_TYPES)
        model = _model(call)
        if not builtin or model is None:
            continue
        facts = GROQ_MODELS.get(model)
        if facts is not None and facts.builtin_tools:
            continue
        names = ", ".join(_shown(t) for t in builtin)
        yield ctx.finding(
            GQ003,
            call,
            f"Built-in tool {names} with model {_shown(model)}: Groq documents built-in tools "
            "only on openai/gpt-oss-20b and openai/gpt-oss-120b (contract R9, T8; models as "
            f"listed on {DATE}); not supported on this model.",
            confidence=confidence,
        )


# --- FC-GQ-004 (contract R4) ----------------------------------------------------------------

GQ004 = _meta(
    "FC-GQ-004",
    "Unsupported Responses parameter",
    Severity.MEDIUM,
    Confidence.HIGH,
    "Remove include, safety_identifier, prompt_cache_key and prompt from /responses requests "
    "to Groq.",
    DOC_RESPONSES,
    priority="P1",
)


@rule(GQ004)
def unsupported_responses_params(index: RepoIndex, ctx: RuleContext) -> Iterator[Finding]:
    for call in ctx.call_sites_for(GQ004):
        if not _is_responses(call):
            continue
        names = [name for name in UNSUPPORTED_RESPONSES_PARAMS if _supplied(call, name)]
        if not names:
            continue
        listed = ", ".join(f"`{name}`" for name in names)
        literal = all(not isinstance(call.params[name], Unknown) for name in names)
        yield ctx.finding(
            GQ004,
            call,
            f"{listed} on /responses: Groq lists these as not supported yet (contract R4); "
            "whether they are rejected or ignored is [UNVERIFIED].",
            confidence=Confidence.HIGH if literal else Confidence.MEDIUM,
        )


# --- FC-GQ-005 (contract §1.8) --------------------------------------------------------------

GQ005 = _meta(
    "FC-GQ-005",
    "Output budget above the model's maximum",
    Severity.MEDIUM,
    Confidence.HIGH,
    "Keep the output budget within the model's documented maximum completion tokens (Groq's "
    "models page); on /responses the budget includes reasoning tokens.",
    DOC_MODELS,
)


@rule(GQ005)
def output_budget(index: RepoIndex, ctx: RuleContext) -> Iterator[Finding]:
    for call in ctx.call_sites_for(GQ005):
        found = _facts(call)
        if found is None or found[1].max_completion_tokens is None:
            continue
        model, facts = found
        limit = facts.max_completion_tokens
        names = (
            ("max_output_tokens",)
            if _is_responses(call)
            else ("max_completion_tokens", "max_tokens")
        )
        for name in names:
            value = call.params.get(name)
            if _is_int(value) and value > limit:
                yield ctx.finding(
                    GQ005,
                    call,
                    f"{name}={value} exceeds the {limit:,} max completion tokens Groq lists "
                    f"for {_shown(model)} (catalogue of {DATE}, contract §1.8); what happens "
                    "above the limit is [UNVERIFIED].",
                )


# --- FC-GQ-006 (contract §1.8, M1, M2) ------------------------------------------------------

GQ006 = _meta(
    "FC-GQ-006",
    "Model not in Groq's catalogue, decommissioned or preview",
    Severity.MEDIUM,
    Confidence.MEDIUM,
    "Use a production model from Groq's models page (e.g. openai/gpt-oss-20b), or one "
    "returned by GET /models for your key; migrate before a model's shutdown date.",
    DOC_MODELS,
)


@rule(GQ006)
def model_catalogue(index: RepoIndex, ctx: RuleContext) -> Iterator[Finding]:
    for call in ctx.call_sites_for(GQ006):
        model = _model(call)
        if model is None:
            continue
        shown = _shown(model)
        if model in GROQ_DECOMMISSIONED_MODELS:
            yield ctx.finding(
                GQ006,
                call,
                f"model {shown} is decommissioned on Groq (deprecations page, contract M2): "
                "requests to decommissioned model IDs return errors. Pick a replacement from "
                f"the catalogue of {DATE}.",
                severity=Severity.HIGH,
                confidence=Confidence.HIGH,
                doc_url=DOC_DEPRECATIONS,
            )
            continue
        facts = GROQ_MODELS.get(model)
        if facts is None:
            yield ctx.finding(
                GQ006,
                call,
                f"model {shown} is not in Groq's model catalogue of {DATE} (contract §1.8); "
                "GET /models lists the models active for your key.",
            )
        elif model in FREE_TIER_SHUTDOWN_MODELS:
            yield ctx.finding(
                GQ006,
                call,
                f"model {shown} is Enterprise-only on Groq (catalogue of {DATE}): it was shut "
                "down for free and developer tiers on 16 Aug 2026 (contract M2), so requests "
                "on those plans fail.",
                severity=Severity.LOW,
                confidence=Confidence.HIGH,
                doc_url=DOC_DEPRECATIONS,
            )
        elif facts.tier == "preview":
            enterprise = " and Enterprise-only" if facts.enterprise_only else ""
            yield ctx.finding(
                GQ006,
                call,
                f"model {shown} is a preview{enterprise} model on Groq (catalogue of {DATE}): "
                "preview models are for evaluation only and may be discontinued at short "
                "notice (contract M1).",
                severity=Severity.INFO,
                confidence=Confidence.HIGH,
            )


# --- FC-GQ-007 (contract C2) ----------------------------------------------------------------

GQ007 = _meta(
    "FC-GQ-007",
    "Chat parameter rejected by Groq",
    Severity.HIGH,
    Confidence.HIGH,
    "Remove logprobs, logit_bias and top_logprobs from Chat Completions requests to Groq.",
    DOC_OPENAI,
)


@rule(GQ007)
def rejected_chat_params(index: RepoIndex, ctx: RuleContext) -> Iterator[Finding]:
    for call in ctx.call_sites_for(GQ007):
        if not _is_chat(call):
            continue
        names = [
            name
            for name in REJECTED_CHAT_PARAMS
            if name in call.params
            and not isinstance(call.params[name], Unknown)
            and call.params[name] is not None
            and call.params[name] is not False
        ]
        if names:
            listed = ", ".join(f"`{name}`" for name in names)
            yield ctx.finding(
                GQ007,
                call,
                f"{listed} supplied: Groq documents logprobs, logit_bias and top_logprobs as "
                "not supported, with HTTP 400 if they are supplied (contract C2); requests from "
                "this call site will be rejected.",
            )


# --- FC-GQ-008 (contract C4) ----------------------------------------------------------------

GQ008 = _meta(
    "FC-GQ-008",
    "n other than 1",
    Severity.HIGH,
    Confidence.HIGH,
    "Drop `n` (or set n=1); make separate requests if you need several completions.",
    DOC_OPENAI,
)


@rule(GQ008)
def n_not_one(index: RepoIndex, ctx: RuleContext) -> Iterator[Finding]:
    for call in ctx.call_sites_for(GQ008):
        value = call.params.get("n")
        if _is_chat(call) and _is_int(value) and value != 1:
            yield ctx.finding(
                GQ008,
                call,
                f"n={value}: Groq supports only n=1 and documents any other value as HTTP 400 "
                "(contract C4); requests from this call site will be rejected.",
            )


# --- FC-GQ-009 (contract C5) ----------------------------------------------------------------

GQ009 = _meta(
    "FC-GQ-009",
    "Parameter with no effect on Groq",
    Severity.LOW,
    Confidence.MEDIUM,
    "Remove frequency_penalty, presence_penalty and metadata from Chat requests to Groq.",
    DOC_API,
    priority="P1",
)


@rule(GQ009)
def no_effect_params(index: RepoIndex, ctx: RuleContext) -> Iterator[Finding]:
    for call in ctx.call_sites_for(GQ009):
        if not _is_chat(call):
            continue
        names = [
            name
            for name in NO_EFFECT_PENALTIES
            if isinstance(call.params.get(name), int | float)
            and not isinstance(call.params.get(name), bool)
            and call.params[name] != 0
        ]
        metadata = call.params.get("metadata")
        if metadata is not None and not isinstance(metadata, Unknown):
            names.append("metadata")
        if names:
            listed = ", ".join(f"`{name}`" for name in names)
            yield ctx.finding(
                GQ009,
                call,
                f"{listed}: Groq's API reference describes the penalties as not yet supported "
                "by any model and metadata as not currently supported (contract C5), so this "
                "has no effect on Groq at best; whether it is ignored or rejected is "
                "[UNVERIFIED].",
            )


# --- FC-GQ-010 (contract C6, C9) ------------------------------------------------------------

GQ010 = _meta(
    "FC-GQ-010",
    "Deprecated Chat parameter",
    Severity.LOW,
    Confidence.HIGH,
    "Use max_completion_tokens instead of max_tokens, and tools/tool_choice instead of "
    "functions/function_call.",
    DOC_API,
    priority="P1",
)
DEPRECATED_CHAT_PARAMS = {
    "max_tokens": "max_completion_tokens",
    "functions": "tools",
    "function_call": "tool_choice",
}


@rule(GQ010)
def deprecated_params(index: RepoIndex, ctx: RuleContext) -> Iterator[Finding]:
    for call in ctx.call_sites_for(GQ010):
        if not _is_chat(call):
            continue
        names = [name for name in DEPRECATED_CHAT_PARAMS if _supplied(call, name)]
        if not names:
            continue
        listed = ", ".join(f"`{name}` (use `{DEPRECATED_CHAT_PARAMS[name]}`)" for name in names)
        literal = all(not isinstance(call.params[name], Unknown) for name in names)
        yield ctx.finding(
            GQ010,
            call,
            f"Deprecated parameter {listed}: Groq's API reference marks it deprecated "
            "(contract C6, C9).",
            confidence=Confidence.HIGH if literal else Confidence.MEDIUM,
        )


# --- FC-GQ-011 (contract S3, S5, T9) --------------------------------------------------------

GQ011 = _meta(
    "FC-GQ-011",
    "Structured output on an unsupported model or mode",
    Severity.MEDIUM,
    Confidence.HIGH,
    "Use json_schema only with a model Groq lists for it (strict: openai/gpt-oss-20b, "
    "openai/gpt-oss-120b, qwen/qwen3.8-27b, meta-models/muse-spark-open); otherwise use JSON "
    "object mode. Do not combine Structured Outputs with streaming or tools.",
    DOC_STRUCTURED,
)


@rule(GQ011)
def structured_output_support(index: RepoIndex, ctx: RuleContext) -> Iterator[Finding]:
    for call in ctx.call_sites_for(GQ011):
        schema = _json_schema(_structured_format(call), call)
        if schema is None:
            continue
        found = _facts(call)
        if found is not None:
            model, facts = found
            if not facts.json_schema:
                yield ctx.finding(
                    GQ011,
                    call,
                    f"json_schema output with model {_shown(model)}: Groq lists JSON schema "
                    f"support only for the models in contract S3 (catalogue of {DATE}); other "
                    "models support JSON object mode only, and what happens otherwise is "
                    "[UNVERIFIED].",
                )
            elif schema.get("strict") is True and not facts.strict_json_schema:
                yield ctx.finding(
                    GQ011,
                    call,
                    f"strict: true with model {_shown(model)}: Groq lists this model for "
                    f"best-effort JSON schema only, not strict mode (contract S3, catalogue of "
                    f"{DATE}).",
                )
        modes = []
        if _streams(call):
            modes.append("streaming")
        if _has_tools(call):
            modes.append("tools")
        if modes:
            yield ctx.finding(
                GQ011,
                call,
                f"Structured Outputs (json_schema) together with {' and '.join(modes)}: Groq "
                "documents Structured Outputs as not supported with streaming or tool use "
                f"(contract S5, T9; docs read {DATE}); the effect is [UNVERIFIED].",
            )


# --- FC-GQ-012 (contract S2) ----------------------------------------------------------------

GQ012 = _meta(
    "FC-GQ-012",
    "Strict schema not strict-compatible",
    Severity.MEDIUM,
    Confidence.MEDIUM,
    "In strict mode, list every property in `required` and set `additionalProperties: false` "
    "on every object; model optional fields as nullable types instead.",
    DOC_STRUCTURED,
    priority="P1",
)


def _strict_problem(schema: Any, where: str = "schema", depth: int = 0) -> str | None:
    """The first object in a literal JSON schema that strict mode rejects (contract S2)."""
    if depth > 20 or not isinstance(schema, dict):
        return None
    properties = schema.get("properties")
    if schema.get("type") == "object" or isinstance(properties, dict):
        if schema.get("additionalProperties") is not False:
            return f"{where} lacks additionalProperties: false"
        if isinstance(properties, dict):
            required = schema.get("required")
            listed = set(required) if isinstance(required, list | tuple) else set()
            missing = sorted(str(name) for name in properties if name not in listed)
            if missing:
                return f"{where} leaves {', '.join(missing[:5])} out of `required`"
            for name, child in properties.items():
                found = _strict_problem(child, f"{where}.{name}", depth + 1)
                if found:
                    return found
    for key in ("items", "$defs", "definitions", "anyOf", "oneOf", "allOf"):
        child = schema.get(key)
        children = (
            list(child.values())
            if isinstance(child, dict) and key in ("$defs", "definitions")
            else child
            if isinstance(child, list)
            else [child]
        )
        for sub in children:
            found = _strict_problem(sub, f"{where}.{key}", depth + 1)
            if found:
                return found
    return None


@rule(GQ012)
def strict_schema(index: RepoIndex, ctx: RuleContext) -> Iterator[Finding]:
    for call in ctx.call_sites_for(GQ012):
        schema = _json_schema(_structured_format(call), call)
        if schema is None or schema.get("strict") is not True:
            continue
        problem = _strict_problem(schema.get("schema"))
        if problem:
            yield ctx.finding(
                GQ012,
                call,
                f"strict: true, but the literal {problem}: Groq's strict mode requires every "
                "property in `required` and `additionalProperties: false` (contract S2).",
            )


# --- FC-GQ-013 (contract S6; heuristic, ADR-013) --------------------------------------------

GQ013 = _meta(
    "FC-GQ-013",
    "JSON object mode without JSON instructions",
    Severity.LOW,
    Confidence.MEDIUM,
    'Tell the model to answer in JSON (e.g. a system message "Respond with a JSON object …") '
    "when using response_format json_object.",
    DOC_STRUCTURED,
    priority="P1",
)


def _texts(value: Any, depth: int = 0) -> list[str]:
    """Every string inside a literal messages list (content strings and text parts)."""
    if depth > 20:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [t for child in value.values() for t in _texts(child, depth + 1)]
    if isinstance(value, list | tuple):
        return [t for child in value for t in _texts(child, depth + 1)]
    return []


@rule(GQ013)
def json_mode_without_instructions(index: RepoIndex, ctx: RuleContext) -> Iterator[Finding]:
    for call in ctx.call_sites_for(GQ013):
        if not _is_chat(call):
            continue
        response_format = call.params.get("response_format")
        if not isinstance(response_format, dict) or response_format.get("type") != "json_object":
            continue
        messages = call.params.get("messages")
        if not isinstance(messages, list | tuple) or not messages:
            continue  # not a literal: the prompt cannot be seen
        if any("json" in text.lower() for text in _texts(messages)):
            continue
        yield ctx.finding(
            GQ013,
            call,
            "JSON object mode, but no mention of JSON observed in the literal `messages` of "
            "this call: Groq's JSON object mode needs explicit JSON instructions in the prompt "
            "(contract S6).",
        )


# --- FC-GQ-014 (contract M4, M5, S8) --------------------------------------------------------

GQ014 = _meta(
    "FC-GQ-014",
    "Invalid reasoning parameters",
    Severity.HIGH,
    Confidence.HIGH,
    "Use a reasoning_effort value the model supports (GPT-OSS: low, medium, high); do not "
    "send reasoning_format to GPT-OSS models, do not combine reasoning_format with "
    'include_reasoning, and use reasoning_format="parsed" or "hidden" with JSON mode or tools.',
    DOC_REASONING,
)


def _effort(call: CallSite) -> str | None:
    if _is_chat(call):
        value = call.params.get("reasoning_effort")
        return value if isinstance(value, str) else None
    reasoning = call.params.get("reasoning")
    if isinstance(reasoning, dict) and isinstance(reasoning.get("effort"), str):
        return reasoning["effort"]
    return None


@rule(GQ014)
def reasoning_params(index: RepoIndex, ctx: RuleContext) -> Iterator[Finding]:
    for call in ctx.call_sites_for(GQ014):
        found = _facts(call)
        effort = _effort(call)
        if found is not None and effort is not None:
            model, facts = found
            if facts.reasoning_efforts is not None and effort not in facts.reasoning_efforts:
                allowed = ", ".join(sorted(facts.reasoning_efforts))
                yield ctx.finding(
                    GQ014,
                    call,
                    f"reasoning effort {_shown(effort)} with model {_shown(model)}: Groq "
                    f"documents {allowed} for this model (catalogue of {DATE}) and rejects other "
                    "values with HTTP 400 (contract M4).",
                )
        if not _is_chat(call):
            continue
        fmt = call.params.get("reasoning_format")
        if not isinstance(fmt, str):
            continue
        if found is not None and found[1].reasoning_format is False:
            yield ctx.finding(
                GQ014,
                call,
                f"reasoning_format with model {_shown(found[0])}: Groq documents "
                f"reasoning_format as not supported on GPT-OSS models (contract M5, catalogue "
                f"of {DATE}); the status code is [UNVERIFIED].",
                severity=Severity.MEDIUM,
            )
        include = call.params.get("include_reasoning")
        if include is not None and not isinstance(include, Unknown):
            yield ctx.finding(
                GQ014,
                call,
                "reasoning_format together with include_reasoning: Groq documents them as "
                f"mutually exclusive (contract M5, docs read {DATE}); the status code is "
                "[UNVERIFIED].",
                severity=Severity.MEDIUM,
            )
        # S8 names JSON mode (json_object); json_schema is not claimed (no documented 400).
        response_format = call.params.get("response_format")
        json_mode = (
            isinstance(response_format, dict) and response_format.get("type") == "json_object"
        )
        if fmt == "raw" and (json_mode or _has_tools(call)):
            what = "JSON mode" if json_mode else "tool use"
            yield ctx.finding(
                GQ014,
                call,
                f'reasoning_format="raw" with {what}: Groq documents this combination as HTTP '
                f"400 (contract S8, docs read {DATE}); requests from this call site will be "
                "rejected.",
            )


# --- FC-GQ-015 (contract A1, K4) ------------------------------------------------------------

GQ015 = _meta(
    "FC-GQ-015",
    "Wrong base URL for the client library",
    Severity.HIGH,
    Confidence.HIGH,
    "groq.Groq: omit base_url (the SDK default is https://api.groq.com and it adds /openai/v1 "
    "itself). OpenAI SDK or raw HTTP: use https://api.groq.com/openai/v1.",
    DOC_SDK,
)
HTTP_CLIENTS = frozenset(
    {"httpx.Client", "httpx.AsyncClient", "httpx2.Client", "httpx2.AsyncClient"}
)


def _base_url_text(binding: ClientBinding) -> str | None:
    if "base_url" not in binding.kwargs:
        return None
    value = binding.kwargs["base_url"]
    if isinstance(value, str):
        return value
    return "".join(binding.base_url_fragments) or None


def _bound_posts(index: RepoIndex, binding: ClientBinding) -> list[CallSite]:
    return [
        call
        for call in index.call_sites
        if call.kind == "http.post" and call.binding is not None and call.binding.id == binding.id
    ]


@rule(GQ015)
def wrong_base_url(index: RepoIndex, ctx: RuleContext) -> Iterator[Finding]:
    for binding in index.clients:
        if not ctx.applies_to_binding(GQ015, binding.id):
            continue
        base = _base_url_text(binding)
        if base is None:
            continue
        path = base.rstrip("/")
        shown = _shown(base)
        message: str | None = None
        doc = DOC_SDK
        if binding.constructor in GROQ_SDK_CONSTRUCTORS:
            if path.endswith(OPENAI_PATH):
                message = (
                    f"`{binding.constructor}` with base_url {shown}: the groq SDK's base URL is "
                    "the host (default https://api.groq.com) and it adds /openai/v1 to every "
                    "path itself (contract K4), so requests go to …/openai/v1/openai/v1/… "
                    "and fail."
                )
        elif GROQ_HOST in base.lower() and OPENAI_PATH not in path:
            if binding.constructor in OPENAI_SDK_CONSTRUCTORS:
                doc = DOC_OPENAI
                message = (
                    f"`{binding.constructor}` with base_url {shown}: Groq's OpenAI-compatible "
                    "base URL is https://api.groq.com/openai/v1 (contract A1); the OpenAI SDK "
                    "appends only /chat/completions or /responses, so requests miss "
                    "/openai/v1 and fail."
                )
            elif binding.constructor in HTTP_CLIENTS:
                posts = [
                    call.url
                    for call in _bound_posts(index, binding)
                    if call.url is not None and OPENAI_PATH not in call.url
                ]
                if posts:
                    doc = DOC_OPENAI
                    message = (
                        f"`{binding.constructor}` with base_url {shown} posts to "
                        f"{_shown(posts[0])}: Groq's endpoints live under "
                        "https://api.groq.com/openai/v1 (contract A1), so this path misses "
                        "/openai/v1."
                    )
        if message is None:
            continue
        finding = ctx.finding_at(GQ015, binding.location, message, doc_url=doc)
        detection = ctx.detection.bindings.get(binding.id)
        if detection is not None:
            finding = finding.model_copy(update={"provider": detection.provider})
        yield finding


# --- FC-GQ-017 (contract R14, K8) -----------------------------------------------------------

GQ017 = _meta(
    "FC-GQ-017",
    "responses called on the groq SDK",
    Severity.HIGH,
    Confidence.MEDIUM,
    "Call Groq's Responses API through the OpenAI SDK "
    '(OpenAI(base_url="https://api.groq.com/openai/v1")) or raw HTTP, or use '
    "chat.completions with the groq SDK.",
    DOC_SDK,
)


@rule(GQ017)
def responses_on_groq_sdk(index: RepoIndex, ctx: RuleContext) -> Iterator[Finding]:
    for call in ctx.call_sites_for(GQ017):
        if not call.kind.startswith("responses.") or call.binding is None:
            continue
        if call.binding.constructor not in GROQ_SDK_CONSTRUCTORS:
            continue
        yield ctx.finding(
            GQ017,
            call,
            f"`{call.kind}` on a `{call.binding.constructor}` client: the groq Python SDK "
            f"(v1.7.0, read {DATE}) has no `responses` resource (contract R14, K8), so "
            "this attribute does not exist unless a later SDK version adds it.",
        )


# --- FC-GQ-018 (contract C7, T1) ------------------------------------------------------------

GQ018 = _meta(
    "FC-GQ-018",
    "Too many stop sequences or tools",
    Severity.MEDIUM,
    Confidence.HIGH,
    "Send at most 4 stop sequences and at most 128 tools (Groq advises 3–5 tools, 10–15 at most).",
    DOC_API,
    priority="P1",
)


@rule(GQ018)
def too_many(index: RepoIndex, ctx: RuleContext) -> Iterator[Finding]:
    for call in ctx.call_sites_for(GQ018):
        stop = call.params.get("stop")
        if _is_chat(call) and isinstance(stop, list | tuple) and len(stop) > MAX_STOP_SEQUENCES:
            yield ctx.finding(
                GQ018,
                call,
                f"{len(stop)} stop sequences: Groq accepts up to {MAX_STOP_SEQUENCES} "
                "(contract C7).",
            )
        tools, confidence = _tools(call)
        if tools is not None and len(tools) > MAX_TOOLS:
            yield ctx.finding(
                GQ018,
                call,
                f"{len(tools)} tools: Groq accepts at most {MAX_TOOLS} functions per request "
                "(contract T1).",
                confidence=confidence,
            )


# --- FC-GQ-019 (contract C10) ---------------------------------------------------------------

GQ019 = _meta(
    "FC-GQ-019",
    "stream_options without streaming",
    Severity.LOW,
    Confidence.HIGH,
    "Set stream_options only together with stream=True.",
    DOC_API,
    priority="P1",
)


@rule(GQ019)
def stream_options_without_stream(index: RepoIndex, ctx: RuleContext) -> Iterator[Finding]:
    for call in ctx.call_sites_for(GQ019):
        # A non-literal ``stream`` (call.stream is None) may be True: not flagged.
        if _is_chat(call) and _supplied(call, "stream_options") and call.stream is False:
            yield ctx.finding(
                GQ019,
                call,
                "stream_options without stream=True: Groq's API reference says stream_options "
                "should only be set together with stream: true (contract C10).",
            )
