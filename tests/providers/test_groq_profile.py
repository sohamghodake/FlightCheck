"""GROQ_PROFILE: the documented Groq capability profile (phase G2, ADR-036)."""

import json

from flightcheck.core.config import GROQ_BASE_URL, GROQ_DOCUMENTED_MODELS
from flightcheck.providers import GROQ_PROFILE, groq_profile
from flightcheck.providers.capabilities import (
    GROQ_SOURCES,
    Capability,
    CapabilityProfile,
    SupportLevel,
)

S = SupportLevel.SUPPORTED_DOCUMENTED
U = SupportLevel.UNSUPPORTED_DOCUMENTED
K = SupportLevel.UNKNOWN

# Hand-written from docs/GROQ_CONTRACT.md §1 (rows in brackets). [UNVERIFIED] facts → unknown.
EXPECTED = {
    Capability.LIST_MODELS: S,  # A5, A6
    Capability.RESPONSES_API: S,  # R1 (beta)
    Capability.CHAT_COMPLETIONS: S,  # A5
    Capability.STREAM_RESPONSES: K,  # R15: no documented events
    Capability.STREAM_CHAT: S,  # ST1
    Capability.FUNCTION_TOOLS: S,  # T1
    Capability.HOSTED_TOOLS: S,  # R9, T8 (GPT-OSS only; noted)
    Capability.STRUCTURED_OUTPUT_TEXT_FORMAT: S,  # R2
    Capability.STRUCTURED_OUTPUT_RESPONSE_FORMAT: S,  # S1
    Capability.STORE_TRUE: U,  # R4, R5
    Capability.PREVIOUS_RESPONSE_ID: U,  # R4, R6
    Capability.BACKGROUND: K,  # R8
    Capability.IMAGE_INPUT: S,  # R2
    Capability.AUDIO_INPUT: K,  # R13
    Capability.VIDEO_INPUT: K,  # R13
    Capability.FILE_INPUT: K,  # R13
    Capability.REASONING_EFFORT_NONE: S,  # M4 (qwen3 only; noted)
}


def test_groq_profile_matches_the_contract() -> None:
    assert set(GROQ_PROFILE.entries) == set(Capability)
    for cap, level in EXPECTED.items():
        assert GROQ_PROFILE.level(cap) is level, cap


def test_every_entry_cites_a_source_and_has_a_note() -> None:
    for cap in Capability:
        entry = GROQ_PROFILE.entry(cap)
        assert entry.source in GROQ_SOURCES.values(), cap
        assert entry.note, cap
        if entry.level is K:
            assert entry.source == GROQ_SOURCES["unverified"], cap
        else:
            assert entry.source.startswith("https://console.groq.com/docs/"), cap


def test_model_dependent_entries_say_so() -> None:
    for cap in (
        Capability.HOSTED_TOOLS,
        Capability.STRUCTURED_OUTPUT_RESPONSE_FORMAT,
        Capability.IMAGE_INPUT,
        Capability.REASONING_EFFORT_NONE,
    ):
        assert "Model-dependent" in GROQ_PROFILE.entry(cap).note, cap


def test_groq_profile_identity_and_models() -> None:
    assert GROQ_PROFILE.provider == "groq"
    assert GROQ_PROFILE.base_url == GROQ_BASE_URL
    assert set(GROQ_PROFILE.documented_models) == GROQ_DOCUMENTED_MODELS
    assert list(GROQ_PROFILE.documented_models) == sorted(GROQ_PROFILE.documented_models)
    assert GROQ_PROFILE.max_output_tokens is None  # per model, not provider-wide


def test_declarations_fill_only_unknown_entries() -> None:
    profile = groq_profile(
        "http://127.0.0.1:9/openai/v1/",
        {Capability.BACKGROUND: True, Capability.STORE_TRUE: True, Capability.FILE_INPUT: False},
    )
    assert profile.base_url == "http://127.0.0.1:9/openai/v1"
    assert profile.level(Capability.BACKGROUND) is SupportLevel.DECLARED
    assert profile.level(Capability.FILE_INPUT) is SupportLevel.DECLARED_UNSUPPORTED
    assert profile.level(Capability.STORE_TRUE) is U  # a documented fact is not declared away
    assert GROQ_PROFILE.level(Capability.BACKGROUND) is K  # the constant is untouched


def test_groq_profile_round_trips_through_json() -> None:
    data = json.loads(GROQ_PROFILE.model_dump_json())
    assert CapabilityProfile.model_validate(data) == GROQ_PROFILE
