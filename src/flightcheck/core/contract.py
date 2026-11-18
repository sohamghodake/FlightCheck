"""Request-contract checks shared by the mock, the provider adapter and the runtime checks
(pure).

BharatCode: the mock answers these with HTTP 400 (ADR-014); the BharatCode adapter refuses them
locally before any network call (plan §14.7). Both use this one implementation so they cannot
drift apart (ADR-028). The facts come from BHARATCODE_CONTRACT.md §15.1, "Responses —
unsupported (→ 400)"; the error codes are [UNVERIFIED].

Groq (ADR-038): :func:`groq_chat_contract_violations` and
:func:`groq_responses_contract_violations` list what docs/GROQ_CONTRACT.md documents as a 400
(rows C2, C4, M4, S8: ``status_documented=True``) and what Groq lists as not supported without
stating the effect (rows R4, R5: ``status_documented=False``; the mock still answers 400 and
labels it [UNVERIFIED], contract §2). ``messages[].name`` (C2 vs C3) and ``truncation`` (R7) are
deliberately not enforced until G5 verifies them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flightcheck.core.config import FILE_INPUT_KEYS, GROQ_MODELS, UNSUPPORTED_INPUT_TYPES

_INPUT_DEPTH_LIMIT = 20

# Groq (docs/GROQ_CONTRACT.md, read 18 Nov 2026).
GROQ_REJECTED_CHAT_PARAMS = ("logprobs", "logit_bias", "top_logprobs")  # C2: 400 if supplied
# R4: listed as "not supported yet" on /responses; the effect of sending them is not stated.
GROQ_UNSUPPORTED_RESPONSES_PARAMS = ("include", "safety_identifier", "prompt_cache_key", "prompt")
GROQ_BUILTIN_TOOL_TYPES = frozenset({"browser_search", "code_interpreter"})  # R9, T8
GROQ_DOCUMENTED_TOOL_TYPES = frozenset({"function", "mcp"}) | GROQ_BUILTIN_TOOL_TYPES  # R9, T8


@dataclass(frozen=True)
class Violation:
    field: str
    code: str  # [UNVERIFIED]: BharatCode's error codes are not documented; Groq documents none
    message: str
    # False: the provider documents the feature as unsupported but not what happens when it is
    # sent (Groq rows R4/R5); whoever answers it with a 400 must label that [UNVERIFIED].
    status_documented: bool = True


def _unsupported_input(value: Any, depth: int = 0) -> list[str]:
    """Same walk as FC-BC-008: unsupported item types and file keys anywhere in ``input``."""
    if depth > _INPUT_DEPTH_LIMIT:
        return []
    found: list[str] = []
    if isinstance(value, dict):
        item_type = value.get("type")
        if isinstance(item_type, str) and item_type in UNSUPPORTED_INPUT_TYPES:
            found.append(item_type)
        found.extend(key for key in FILE_INPUT_KEYS if key in value)
        for child in value.values():
            found.extend(_unsupported_input(child, depth + 1))
    elif isinstance(value, list):
        for child in value:
            found.extend(_unsupported_input(child, depth + 1))
    return found


def _hosted_tool_types(tools: Any) -> list[str]:
    if not isinstance(tools, list):
        return []
    found = {
        tool["type"]
        for tool in tools
        if isinstance(tool, dict) and isinstance(tool.get("type"), str)
        if tool["type"] != "function"
    }
    return sorted(found)


def responses_contract_violations(request_json: dict[str, Any]) -> list[Violation]:
    """The /responses features BharatCode documents as unsupported (BHARATCODE_CONTRACT.md
    §15.1, "Responses — unsupported (→ 400)"), in a fixed order. Mirrors FC-BC-001…004/008."""
    found: list[Violation] = []
    if request_json.get("store") is True:
        found.append(
            Violation(
                "store",
                "unsupported_parameter",
                "store=true is not supported: send store=false and replay history in input.",
            )
        )
    # A JSON null is treated as "not used"; whether BharatCode rejects the key itself is
    # [UNVERIFIED].
    if request_json.get("previous_response_id") is not None:
        found.append(
            Violation(
                "previous_response_id",
                "unsupported_parameter",
                "previous_response_id is not supported: the Responses API is stateless; "
                "replay prior turns and output items in input.",
            )
        )
    hosted = _hosted_tool_types(request_json.get("tools"))
    if hosted:
        found.append(
            Violation(
                "tools",
                "unsupported_tool_type",
                f"Hosted tools are not supported ({', '.join(t[:80] for t in hosted)}): "
                "define your own function tools.",
            )
        )
    if request_json.get("background") is True:
        found.append(
            Violation(
                "background",
                "unsupported_parameter",
                "background=true is not supported: run the request synchronously.",
            )
        )
    unsupported = sorted(set(_unsupported_input(request_json.get("input"))))
    if unsupported:
        found.append(
            Violation(
                "input",
                "unsupported_input_type",
                f"Unsupported input ({', '.join(unsupported)}): audio, video and file input are "
                "not supported; send text or images.",
            )
        )
    return found


# --- Groq (ADR-038) -------------------------------------------------------------------------


def _supplied(value: Any) -> bool:
    """A parameter counts as supplied unless it is absent, null or false (the FC-GQ-007
    reading of C2: ``logprobs=False`` asks for nothing)."""
    return value is not None and value is not False


def _int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def groq_reasoning_violation(model: Any, effort: Any) -> Violation | None:
    """M4: a ``reasoning_effort`` outside the model's documented set is a 400. Judged only for
    catalogue models that document their values (``GroqModelFacts.reasoning_efforts``)."""
    if not isinstance(model, str) or not isinstance(effort, str):
        return None
    facts = GROQ_MODELS.get(model)
    if facts is None or facts.reasoning_efforts is None or effort in facts.reasoning_efforts:
        return None
    allowed = ", ".join(sorted(facts.reasoning_efforts))
    return Violation(
        "reasoning_effort",
        "invalid_reasoning_effort",
        f"reasoning effort {effort[:40]!r} is not supported by {model}: use {allowed} "
        "(contract M4).",
    )


def groq_chat_contract_violations(request_json: dict[str, Any]) -> list[Violation]:
    """Chat Completions requests Groq documents as HTTP 400, in a fixed order (contract C2, C4,
    M4, S8). Mirrors FC-GQ-007, FC-GQ-008 and the 400 cases of FC-GQ-014."""
    found: list[Violation] = []
    rejected = [p for p in GROQ_REJECTED_CHAT_PARAMS if _supplied(request_json.get(p))]
    if rejected:
        found.append(
            Violation(
                rejected[0],
                "unsupported_parameter",
                f"{', '.join(rejected)}: not supported on Groq (contract C2).",
            )
        )
    n = request_json.get("n")
    if _int(n) and n != 1:
        found.append(Violation("n", "invalid_n", "n must be 1 on Groq (contract C4)."))
    reasoning = groq_reasoning_violation(
        request_json.get("model"), request_json.get("reasoning_effort")
    )
    if reasoning is not None:
        found.append(reasoning)
    if request_json.get("reasoning_format") == "raw":
        response_format = request_json.get("response_format")
        json_mode = (
            isinstance(response_format, dict) and response_format.get("type") == "json_object"
        )
        tools = request_json.get("tools")
        if json_mode or (isinstance(tools, list) and tools):
            what = "JSON mode" if json_mode else "tool use"
            found.append(
                Violation(
                    "reasoning_format",
                    "invalid_reasoning_format",
                    f'reasoning_format="raw" cannot be combined with {what} (contract S8).',
                )
            )
    return found


def groq_responses_contract_violations(request_json: dict[str, Any]) -> list[Violation]:
    """/responses requests Groq rejects or lists as not supported, in a fixed order.

    ``store`` other than false/null (R5) and ``previous_response_id``, ``include``,
    ``safety_identifier``, ``prompt_cache_key``, ``prompt`` (R4) carry
    ``status_documented=False``: Groq does not say they return 400 (contract §2). A
    ``reasoning.effort`` outside the model's set is a documented 400 (M4).
    """
    found: list[Violation] = []
    if request_json.get("store") is True:
        found.append(
            Violation(
                "store",
                "unsupported_parameter",
                "store=true is not supported on Groq: send store=false or omit it (contract R5).",
                status_documented=False,
            )
        )
    if request_json.get("previous_response_id") is not None:
        found.append(
            Violation(
                "previous_response_id",
                "unsupported_parameter",
                "previous_response_id is not supported on Groq: replay the conversation in "
                "input (contract R3, R4).",
                status_documented=False,
            )
        )
    for name in GROQ_UNSUPPORTED_RESPONSES_PARAMS:
        if request_json.get(name) is not None:
            found.append(
                Violation(
                    name,
                    "unsupported_parameter",
                    f"{name} is listed as not supported yet on Groq's /responses (contract R4).",
                    status_documented=False,
                )
            )
    reasoning = request_json.get("reasoning")
    effort = reasoning.get("effort") if isinstance(reasoning, dict) else None
    violation = groq_reasoning_violation(request_json.get("model"), effort)
    if violation is not None:
        found.append(violation)
    return found
