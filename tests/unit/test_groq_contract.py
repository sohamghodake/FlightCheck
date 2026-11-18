"""core.contract, Groq part (phase G4, ADR-038): the request facts the mock and checks share."""

import pytest

from flightcheck.core.contract import (
    GROQ_BUILTIN_TOOL_TYPES,
    GROQ_DOCUMENTED_TOOL_TYPES,
    GROQ_REJECTED_CHAT_PARAMS,
    GROQ_UNSUPPORTED_RESPONSES_PARAMS,
    groq_chat_contract_violations,
    groq_reasoning_violation,
    groq_responses_contract_violations,
    responses_contract_violations,
)
from flightcheck.scanner.rules import groq as gq


def test_constants_match_the_static_rules() -> None:
    """The mock/runtime and FC-GQ-* read the same contract rows (C2, R4, R9, T8)."""
    assert GROQ_REJECTED_CHAT_PARAMS == gq.REJECTED_CHAT_PARAMS
    assert GROQ_UNSUPPORTED_RESPONSES_PARAMS == gq.UNSUPPORTED_RESPONSES_PARAMS
    assert GROQ_BUILTIN_TOOL_TYPES == gq.BUILTIN_TOOL_TYPES
    assert GROQ_DOCUMENTED_TOOL_TYPES == gq.DOCUMENTED_TOOL_TYPES


def test_chat_violations_are_documented_400s_in_a_fixed_order() -> None:
    body = {
        "model": "openai/gpt-oss-20b",
        "logit_bias": {"1": 1},
        "logprobs": True,
        "n": 3,
        "reasoning_effort": "none",
        "reasoning_format": "raw",
        "tools": [{"type": "function"}],
    }
    found = groq_chat_contract_violations(body)
    assert [v.field for v in found] == ["logprobs", "n", "reasoning_effort", "reasoning_format"]
    assert all(v.status_documented for v in found)
    assert "logprobs, logit_bias" in found[0].message and "C2" in found[0].message


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"logprobs": False, "logit_bias": None, "top_logprobs": None, "n": 1},
        {"n": True},  # a bool is not an int here
        {"reasoning_format": "raw", "tools": []},
        {"reasoning_format": "raw", "response_format": {"type": "json_schema"}},  # S8: JSON mode
        {"model": "llama-3.3-70b-versatile", "reasoning_effort": "high"},  # values undocumented
        {"messages": [{"role": "tool", "name": "f", "content": "x"}]},  # C2/C3: held for G5
    ],
)
def test_chat_bodies_without_documented_400s(body: dict) -> None:
    assert groq_chat_contract_violations(body) == []


def test_reasoning_effort_m4() -> None:
    assert groq_reasoning_violation("openai/gpt-oss-120b", "none") is not None
    assert groq_reasoning_violation("openai/gpt-oss-120b", "high") is None
    assert groq_reasoning_violation("qwen/qwen3.8-27b", "none") is None
    assert groq_reasoning_violation("meta-models/muse-spark-open", "xhigh") is None
    assert groq_reasoning_violation("unknown/model", "none") is None
    assert groq_reasoning_violation(None, "none") is None


def test_responses_violations_flag_unverified_effects() -> None:
    body = {
        "store": True,
        "previous_response_id": "resp_1",
        "include": [],
        "safety_identifier": "u",
        "prompt_cache_key": "k",
        "prompt": {"id": "p"},
        "model": "openai/gpt-oss-20b",
        "reasoning": {"effort": "none"},
    }
    found = groq_responses_contract_violations(body)
    assert [v.field for v in found] == [
        "store",
        "previous_response_id",
        "include",
        "safety_identifier",
        "prompt_cache_key",
        "prompt",
        "reasoning_effort",
    ]
    assert [v.status_documented for v in found] == [False] * 6 + [True]  # only M4 is a 400


@pytest.mark.parametrize(
    "body",
    [
        {"store": False},
        {"store": None, "previous_response_id": None, "include": None},
        {"tools": [{"type": "browser_search"}], "background": True, "truncation": "auto"},
    ],
)
def test_responses_bodies_groq_does_not_reject(body: dict) -> None:
    assert groq_responses_contract_violations(body) == []


def test_bharatcode_contract_is_unchanged() -> None:
    found = responses_contract_violations({"store": True, "tools": [{"type": "browser_search"}]})
    assert [(v.field, v.code) for v in found] == [
        ("store", "unsupported_parameter"),
        ("tools", "unsupported_tool_type"),
    ]
    assert all(v.status_documented for v in found)
