"""Capability profiles (plan §14.2): the documented BharatCode profile and the generic one.

The enums and models live in :mod:`flightcheck.core.capabilities` (core must not import
providers) and are re-exported here.

Every BharatCode entry comes from BHARATCODE_CONTRACT.md §15.1 (source
https://bharatcode.ai/build, read 6 Nov 2026): documented support → ``supported_documented``,
documented 400s → ``unsupported_documented``, the "Not documented" table → ``unknown``. The
Phase 10 probe upgrades entries to ``*_verified``.
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
    MAX_OUTPUT_TOKENS_CAP,
)

__all__ = [
    "BHARATCODE_PROFILE",
    "BHARATCODE_SOURCE",
    "DECLARED_SOURCE",
    "UNSUPPORTED_LEVELS",
    "Capability",
    "CapabilityEntry",
    "CapabilityProfile",
    "SupportLevel",
    "bharatcode_profile",
    "generic_profile",
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
