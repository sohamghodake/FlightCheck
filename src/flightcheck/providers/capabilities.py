"""Capability profiles (plan §14.2): the documented BharatCode and Groq profiles, and the
generic one.

The enums and models live in :mod:`flightcheck.core.capabilities` (core must not import
providers) and are re-exported here.

Every BharatCode entry comes from BHARATCODE_CONTRACT.md §15.1 (source
https://bharatcode.ai/build, read 6 Nov 2026): documented support → ``supported_documented``,
documented 400s → ``unsupported_documented``, the "Not documented" table → ``unknown``. The
Phase 10 probe upgrades entries to ``*_verified``.

Every Groq entry comes from docs/GROQ_CONTRACT.md §1 (Groq's docs, read 18 Nov 2026; ADR-036):
plainly documented support → ``supported_documented``; documented as unsupported →
``unsupported_documented``; anything [UNVERIFIED] there → ``unknown``. Groq support is often
model-dependent; such entries say so in their note, and the profile stays provider-level.
Phase G5 upgrades entries to ``*_verified``.
"""

from __future__ import annotations

from collections.abc import Mapping

from flightcheck.core.capabilities import (
    UNSUPPORTED_LEVELS,
    Capability,
    CapabilityEntry,
    CapabilityProfile,
    SupportLevel,
)
from flightcheck.core.config import (
    BHARATCODE_BASE_URL,
    BHARATCODE_DOCUMENTED_MODELS,
    GROQ_BASE_URL,
    GROQ_DOCUMENTED_MODELS,
    MAX_OUTPUT_TOKENS_CAP,
)

__all__ = [
    "BHARATCODE_PROFILE",
    "BHARATCODE_SOURCE",
    "DECLARED_SOURCE",
    "GROQ_PROFILE",
    "GROQ_SOURCES",
    "UNSUPPORTED_LEVELS",
    "Capability",
    "CapabilityEntry",
    "CapabilityProfile",
    "SupportLevel",
    "bharatcode_profile",
    "generic_profile",
    "groq_profile",
]

BHARATCODE_SOURCE = "https://bharatcode.ai/build"
DECLARED_SOURCE = "flightcheck.toml"

_S = SupportLevel.SUPPORTED_DOCUMENTED
_U = SupportLevel.UNSUPPORTED_DOCUMENTED
_K = SupportLevel.UNKNOWN

_BHARATCODE_FACTS: dict[Capability, tuple[SupportLevel, str]] = {
    Capability.LIST_MODELS: (_S, "GET /models lists the models eligible for your key."),
    Capability.RESPONSES_API: (_S, "POST /responses; use store:false."),
    Capability.CHAT_COMPLETIONS: (_S, "POST /chat/completions."),
    Capability.STREAM_RESPONSES: (
        _S,
        "stream:true → SSE; finishes on response.completed or response.incomplete; "
        "response.failed is an error; payload fields beyond event names are not documented.",
    ),
    Capability.STREAM_CHAT: (
        _K,
        "Not documented: chunk shape and the data: [DONE] terminator.",
    ),
    Capability.FUNCTION_TOOLS: (
        _S,
        "Own function tools; the application validates arguments and executes them.",
    ),
    Capability.HOSTED_TOOLS: (_U, "Hosted tools are rejected with HTTP 400 on /responses."),
    Capability.STRUCTURED_OUTPUT_TEXT_FORMAT: (
        _S,
        "Structured output through text.format; the exact sub-schema is not documented.",
    ),
    Capability.STRUCTURED_OUTPUT_RESPONSE_FORMAT: (
        _K,
        "Not documented: only Responses text.format is documented.",
    ),
    Capability.STORE_TRUE: (_U, "store:true is rejected with HTTP 400 on /responses."),
    Capability.PREVIOUS_RESPONSE_ID: (
        _U,
        "previous_response_id is rejected with HTTP 400; the Responses API is stateless.",
    ),
    Capability.BACKGROUND: (_U, "Background jobs are rejected with HTTP 400 on /responses."),
    Capability.IMAGE_INPUT: (_S, "Text and image input are supported on /responses."),
    Capability.AUDIO_INPUT: (_U, "Audio input is rejected with HTTP 400 on /responses."),
    Capability.VIDEO_INPUT: (_U, "Video input is rejected with HTTP 400 on /responses."),
    Capability.FILE_INPUT: (_U, "File input is rejected with HTTP 400 on /responses."),
    Capability.REASONING_EFFORT_NONE: (
        _S,
        'reasoning:{effort:"none"} / reasoning_effort:"none" for short answers; other values '
        "are not documented.",
    ),
}


def _bharatcode_entries() -> dict[Capability, CapabilityEntry]:
    return {
        cap: CapabilityEntry(level=level, source=BHARATCODE_SOURCE, note=note)
        for cap, (level, note) in _BHARATCODE_FACTS.items()
    }


def _declared_entry(supported: bool) -> CapabilityEntry:
    if supported:
        return CapabilityEntry(level=SupportLevel.DECLARED, source=DECLARED_SOURCE)
    return CapabilityEntry(level=SupportLevel.DECLARED_UNSUPPORTED, source=DECLARED_SOURCE)


_GROQ_DOCS = "https://console.groq.com/docs/"
# Source URL per contract key (docs/GROQ_CONTRACT.md "Sources").
GROQ_SOURCES = {
    "api": _GROQ_DOCS + "api-reference",
    "models": _GROQ_DOCS + "models",
    "openai": _GROQ_DOCS + "openai",
    "reason": _GROQ_DOCS + "reasoning",
    "resp": _GROQ_DOCS + "responses-api",
    "so": _GROQ_DOCS + "structured-outputs",
    "tools": _GROQ_DOCS + "tool-use/overview",
    "unverified": "docs/GROQ_CONTRACT.md#2-not-documented-or-contradicted--g5-verifies-live",
}

# (level, contract source key, note). Contract row IDs are given in each note.
_GROQ_FACTS: dict[Capability, tuple[SupportLevel, str, str]] = {
    Capability.LIST_MODELS: (
        _S,
        "models",
        "GET /models lists all active models (A5, A6); the field shape is not documented.",
    ),
    Capability.RESPONSES_API: (
        _S,
        "resp",
        "POST /responses is in beta; the groq Python SDK has no responses resource (R1, R14).",
    ),
    Capability.CHAT_COMPLETIONS: (_S, "api", "POST /chat/completions (A5, C1)."),
    Capability.STREAM_RESPONSES: (
        _K,
        "unverified",
        "stream is accepted on /responses, but Groq documents no event names or payloads (R15).",
    ),
    Capability.STREAM_CHAT: (
        _S,
        "api",
        "Data-only SSE ending with data: [DONE]; mid-stream errors use x_groq.error (ST1, ST3).",
    ),
    Capability.FUNCTION_TOOLS: (
        _S,
        "tools",
        "Function tools on every chat model, at most 128 per request (T1, T4, T5).",
    ),
    Capability.HOSTED_TOOLS: (
        _S,
        "resp",
        "Model-dependent: browser_search and code_interpreter on openai/gpt-oss-20b and "
        "openai/gpt-oss-120b only; mcp tools on /responses (R9, T8). Other models: unknown.",
    ),
    Capability.STRUCTURED_OUTPUT_TEXT_FORMAT: (
        _S,
        "resp",
        "Responses text.format with type json_schema (R2); model list as for response_format.",
    ),
    Capability.STRUCTURED_OUTPUT_RESPONSE_FORMAT: (
        _S,
        "so",
        "Model-dependent: json_schema (strict on the models listed in S3), json_object on all "
        "models; not with streaming or tools (S1-S6).",
    ),
    Capability.STORE_TRUE: (
        _U,
        "api",
        "store accepts only false or null on /responses (R4, R5); the effect of true is "
        "[UNVERIFIED].",
    ),
    Capability.PREVIOUS_RESPONSE_ID: (
        _U,
        "resp",
        "Not supported; the response field is always null; replay history in input (R3, R4, "
        "R6). The effect of sending it is [UNVERIFIED].",
    ),
    Capability.BACKGROUND: (
        _K,
        "unverified",
        "background is not a documented request field (R8).",
    ),
    Capability.IMAGE_INPUT: (
        _S,
        "resp",
        "Model-dependent: image input on vision-capable models (R2).",
    ),
    Capability.AUDIO_INPUT: (_K, "unverified", "Not documented for /responses (R13)."),
    Capability.VIDEO_INPUT: (_K, "unverified", "Not documented for /responses (R13)."),
    Capability.FILE_INPUT: (_K, "unverified", "Not documented for /responses (R13)."),
    Capability.REASONING_EFFORT_NONE: (
        _S,
        "api",
        'Model-dependent: reasoning_effort "none" on qwen3 models only; GPT-OSS rejects it with '
        "400 (M4).",
    ),
}


def _groq_entries() -> dict[Capability, CapabilityEntry]:
    return {
        cap: CapabilityEntry(level=level, source=GROQ_SOURCES[key], note=note)
        for cap, (level, key, note) in _GROQ_FACTS.items()
    }


BHARATCODE_PROFILE = CapabilityProfile(
    provider="bharatcode",
    base_url=BHARATCODE_BASE_URL,
    documented_models=tuple(sorted(BHARATCODE_DOCUMENTED_MODELS)),
    max_output_tokens=MAX_OUTPUT_TOKENS_CAP,
    entries=_bharatcode_entries(),
)


def bharatcode_profile(
    base_url: str = BHARATCODE_BASE_URL,
    declared: Mapping[Capability, bool] | None = None,
) -> CapabilityProfile:
    """A fresh copy of the documented profile for ``base_url``.

    Declarations from ``flightcheck.toml`` fill only ``unknown`` entries: a user cannot
    declare away a documented fact (e.g. ``store_true``).
    """
    entries = _bharatcode_entries()
    for cap, supported in (declared or {}).items():
        if entries[cap].level is SupportLevel.UNKNOWN:
            entries[cap] = _declared_entry(supported)
    return BHARATCODE_PROFILE.model_copy(
        update={"base_url": base_url.rstrip("/"), "entries": entries}
    )


GROQ_PROFILE = CapabilityProfile(
    provider="groq",
    base_url=GROQ_BASE_URL,
    documented_models=tuple(sorted(GROQ_DOCUMENTED_MODELS)),
    # No provider-wide budget: Groq's limit is per model (GROQ_MODELS, contract §1.8).
    max_output_tokens=None,
    entries=_groq_entries(),
)


def groq_profile(
    base_url: str = GROQ_BASE_URL,
    declared: Mapping[Capability, bool] | None = None,
) -> CapabilityProfile:
    """A fresh copy of the documented Groq profile; declarations fill only ``unknown`` entries
    (the same rule as :func:`bharatcode_profile`, ADR-028 item 2)."""
    entries = _groq_entries()
    for cap, supported in (declared or {}).items():
        if entries[cap].level is SupportLevel.UNKNOWN:
            entries[cap] = _declared_entry(supported)
    return GROQ_PROFILE.model_copy(update={"base_url": base_url.rstrip("/"), "entries": entries})


def generic_profile(
    declared: Mapping[Capability, bool] | None = None,
    base_url: str = "",
    provider: str = "openai_compatible",
) -> CapabilityProfile:
    """Every capability ``unknown`` except what the user declared (``declared`` /
    ``declared_unsupported``)."""
    entries = {
        cap: CapabilityEntry(level=SupportLevel.UNKNOWN, source="default") for cap in Capability
    }
    for cap, supported in (declared or {}).items():
        entries[Capability(cap)] = _declared_entry(supported)
    return CapabilityProfile(provider=provider, base_url=base_url.rstrip("/"), entries=entries)
