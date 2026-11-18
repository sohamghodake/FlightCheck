"""Groq contract rules FC-GQ-* (phase G3, ADR-037): edge cases beyond the fixture manifest."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from flightcheck.core.config import (
    GROQ_CATALOGUE_DATE,
    GROQ_DECOMMISSIONED_MODELS,
    GROQ_MODELS,
    FlightCheckConfig,
)
from flightcheck.core.models import Finding, Report, ScanRequest
from flightcheck.orchestrator import run_scan
from flightcheck.scanner.rules.groq import FREE_TIER_SHUTDOWN_MODELS

REPO_ROOT = Path(__file__).resolve().parents[2]
RULES = REPO_ROOT / "tests" / "fixtures" / "rules"
GROQ = "import groq\nfrom groq import Groq\nclient = Groq(timeout=60.0)\n"
COMPAT = (
    "import os\nimport openai\nfrom openai import OpenAI\n"
    "client = OpenAI(base_url='https://api.groq.com/openai/v1', "
    "api_key=os.environ['GROQ_API_KEY'], timeout=60.0)\n"
)


def _scan(tmp_path: Path, source: str, override: str | None = None) -> Report:
    (tmp_path / "app.py").write_text(source, encoding="utf-8")
    return run_scan(ScanRequest(root=tmp_path, provider_override=override), FlightCheckConfig())


def _chat(tmp_path: Path, args: str) -> list[Finding]:
    """FC-GQ findings for one Groq chat call with ``args``."""
    source = f"{GROQ}def ask(messages, flag):\n    return client.chat.completions.create({args})\n"
    return [f for f in _scan(tmp_path, source).findings if f.rule_id.startswith("FC-GQ-")]


def _responses(tmp_path: Path, args: str) -> list[Finding]:
    source = f"{COMPAT}def ask(messages, flag):\n    return client.responses.create({args})\n"
    return [f for f in _scan(tmp_path, source).findings if f.rule_id.startswith("FC-GQ-")]


def _ids(findings: list[Finding]) -> list[str]:
    return [f.rule_id for f in findings]


M = "model='openai/gpt-oss-20b', messages=messages"
R = "model='openai/gpt-oss-20b', input=messages"


@pytest.fixture(scope="module")
def fixture_report() -> Report:
    return run_scan(ScanRequest(root=RULES), FlightCheckConfig())


# --- every rule ------------------------------------------------------------------------------


def test_every_groq_finding_cites_the_contract_and_a_groq_source(fixture_report: Report) -> None:
    found = [f for f in fixture_report.findings if f.rule_id.startswith("FC-GQ-")]
    assert len({f.rule_id for f in found}) == 18
    for f in found:
        assert "contract" in f.message, f.message
        assert f.doc_url and "groq" in f.doc_url
        assert f.provider == "groq"
        assert f.kind.value == "static" and f.verification.value == "static"


def test_http_status_is_claimed_only_for_status_backed_rows(fixture_report: Report) -> None:
    """Contract rows with a documented status: C2, C4, M4, S8, T3 (prompt, §2)."""
    for f in fixture_report.findings:
        if not f.rule_id.startswith("FC-GQ-"):
            continue
        if re.search(r"\b(HTTP )?4\d\d\b", f.message):
            assert re.search(r"contract (C2|C4|M4|S8|T3)\b", f.message), f.message
        if "will be rejected" in f.message:
            assert "HTTP 400" in f.message, f.message


def test_model_rules_carry_the_catalogue_date(fixture_report: Report) -> None:
    for f in fixture_report.findings:
        if f.rule_id in ("FC-GQ-005", "FC-GQ-006", "FC-GQ-011", "FC-GQ-014"):
            assert GROQ_CATALOGUE_DATE in f.message, f.message


# --- FC-GQ-001 / 002 / 004 -------------------------------------------------------------------


def test_store_false_none_and_chat_store_are_not_fc_gq_001(tmp_path: Path) -> None:
    assert _responses(tmp_path, f"{R}, store=False") == []
    assert _responses(tmp_path, f"{R}, store=None") == []
    assert _responses(tmp_path, f"{R}, store=flag") == []
    assert "FC-GQ-001" not in _ids(_chat(tmp_path, f"{M}, store=True"))


def test_previous_response_id_none_is_fine(tmp_path: Path) -> None:
    assert _responses(tmp_path, f"{R}, previous_response_id=None") == []


def test_unsupported_responses_param_from_a_variable_is_medium(tmp_path: Path) -> None:
    (found,) = _responses(tmp_path, f"{R}, safety_identifier=flag")
    assert (found.rule_id, found.confidence.value) == ("FC-GQ-004", "medium")
    assert "[UNVERIFIED]" in found.message
    assert _responses(tmp_path, f"{R}, truncation='auto'") == []  # R7 conflict: no rule


# --- FC-GQ-003 -------------------------------------------------------------------------------


def test_builtin_tool_with_unknown_or_missing_model(tmp_path: Path) -> None:
    tools = "tools=[{'type': 'code_interpreter', 'container': {'type': 'auto'}}]"
    assert _ids(_chat(tmp_path, f"model=flag, messages=messages, {tools}")) == []
    found = _chat(tmp_path, f"model='my-model', messages=messages, {tools}")
    assert sorted(_ids(found)) == ["FC-GQ-003", "FC-GQ-006"]


def test_mcp_and_function_tools_are_documented(tmp_path: Path) -> None:
    tools = "tools=[{'type': 'mcp', 'server_url': 'https://x'}, {'type': 'function'}]"
    assert _responses(tmp_path, f"{R}, {tools}") == []


# --- FC-GQ-005 -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "value", "flagged"),
    [
        ("openai/gpt-oss-20b", 65_536, False),
        ("openai/gpt-oss-20b", 65_537, True),
        ("qwen/qwen3.8-27b", 16_385, True),
        ("my-model", 10**9, False),  # unknown limit: FC-GQ-006 only
    ],
)
def test_budget_against_the_model_limit(
    tmp_path: Path, model: str, value: int, flagged: bool
) -> None:
    found = _chat(tmp_path, f"model='{model}', messages=messages, max_completion_tokens={value}")
    assert ("FC-GQ-005" in _ids(found)) is flagged


def test_deprecated_max_tokens_is_checked_too(tmp_path: Path) -> None:
    found = _chat(tmp_path, f"{M}, max_tokens=70000")
    assert sorted(_ids(found)) == ["FC-GQ-005", "FC-GQ-010"]


# --- FC-GQ-006 -------------------------------------------------------------------------------


def test_catalogue_sets_are_consistent() -> None:
    assert FREE_TIER_SHUTDOWN_MODELS == {"llama-3.3-70b-versatile", "llama-3.1-8b-instant"}
    assert not GROQ_DECOMMISSIONED_MODELS & set(GROQ_MODELS)


def test_enterprise_preview_model_is_info(tmp_path: Path) -> None:
    (found,) = _chat(tmp_path, "model='meta-models/muse-spark-open', messages=messages")
    assert (found.severity.value, found.confidence.value) == ("info", "high")
    assert "preview and Enterprise-only" in found.message


def test_production_model_and_variable_model_are_fine(tmp_path: Path) -> None:
    assert _chat(tmp_path, M) == []
    assert _chat(tmp_path, "model=flag, messages=messages") == []


# --- FC-GQ-007 / 008 / 009 / 010 -------------------------------------------------------------


def test_rejected_params_need_a_literal_other_than_none_or_false(tmp_path: Path) -> None:
    assert _chat(tmp_path, f"{M}, logprobs=flag") == []
    assert _chat(tmp_path, f"{M}, logprobs=False, logit_bias=None") == []
    (found,) = _chat(tmp_path, f"{M}, top_logprobs=0")
    assert found.rule_id == "FC-GQ-007" and "HTTP 400" in found.message
    assert _responses(tmp_path, f"{R}, top_logprobs=3") == []  # C2 is a Chat row


def test_n(tmp_path: Path) -> None:
    assert _chat(tmp_path, f"{M}, n=1") == []
    assert _chat(tmp_path, f"{M}, n=flag") == []
    assert _ids(_chat(tmp_path, f"{M}, n=0")) == ["FC-GQ-008"]


def test_penalties(tmp_path: Path) -> None:
    assert _chat(tmp_path, f"{M}, presence_penalty=0.0, frequency_penalty=flag") == []
    (found,) = _chat(tmp_path, f"{M}, frequency_penalty=-0.5")
    assert found.rule_id == "FC-GQ-009" and "has no effect" in found.message


def test_deprecated_params(tmp_path: Path) -> None:
    assert _chat(tmp_path, f"{M}, max_tokens=None") == []
    (found,) = _chat(tmp_path, f"{M}, max_tokens=flag")
    assert (found.rule_id, found.confidence.value) == ("FC-GQ-010", "medium")


# --- FC-GQ-011 / 012 / 013 -------------------------------------------------------------------

SCHEMA = (
    "{'type': 'object', 'properties': {'a': {'type': 'string'}}, 'required': ['a'], "
    "'additionalProperties': False}"
)


def _rf(strict: bool, schema: str = SCHEMA) -> str:
    return (
        "response_format={'type': 'json_schema', 'json_schema': "
        f"{{'name': 'x', 'strict': {strict}, 'schema': {schema}}}}}"
    )


def test_structured_output_with_tools_one_hop(tmp_path: Path) -> None:
    source = (
        f"{GROQ}TOOLS = [{{'type': 'function', 'function': {{'name': 'f'}}}}]\n"
        f"def ask(messages):\n    return client.chat.completions.create({M}, tools=TOOLS, "
        f"{_rf(True)})\n"
    )
    found = [f for f in _scan(tmp_path, source).findings if f.rule_id.startswith("FC-GQ-")]
    assert _ids(found) == ["FC-GQ-011"] and "tools" in found[0].message


def test_json_object_mode_and_text_format_text_are_not_structured_outputs(tmp_path: Path) -> None:
    assert _chat(tmp_path, f"{M}, stream=True, response_format={{'type': 'json_object'}}") == []
    assert _responses(tmp_path, f"{R}, stream=True, text={{'format': {{'type': 'text'}}}}") == []


def test_strict_on_strict_models_is_fine(tmp_path: Path) -> None:
    found = _chat(tmp_path, f"model='qwen/qwen3.8-27b', messages=messages, {_rf(True)}")
    assert _ids(found) == ["FC-GQ-006"]  # only the preview note
    assert _chat(tmp_path, f"model='openai/gpt-oss-120b', messages=messages, {_rf(True)}") == []


@pytest.mark.parametrize(
    "schema",
    [
        "{'type': 'object', 'properties': {'a': {'anyOf': [{'type': 'object', "
        "'properties': {'b': {'type': 'string'}}, 'required': ['b']}]}}, 'required': ['a'], "
        "'additionalProperties': False}",
        "{'$defs': {'Item': {'type': 'object', 'properties': {'n': {'type': 'string'}}, "
        "'additionalProperties': False}}, 'type': 'object', 'properties': {}, "
        "'required': [], 'additionalProperties': False}",
    ],
)
def test_nested_strict_problems_are_found(tmp_path: Path, schema: str) -> None:
    found = _chat(tmp_path, f"{M}, {_rf(True, schema)}")
    assert _ids(found) == ["FC-GQ-012"]
    assert found[0].confidence.value == "medium"


def test_json_mode_heuristic(tmp_path: Path) -> None:
    rf = "response_format={'type': 'json_object'}"
    parts = "[{'role': 'user', 'content': [{'type': 'text', 'text': 'Answer as JSON.'}]}]"
    assert _chat(tmp_path, f"model='openai/gpt-oss-20b', messages={parts}, {rf}") == []
    found = _chat(
        tmp_path,
        f"model='openai/gpt-oss-20b', messages=[{{'role': 'user', 'content': 'hi'}}], {rf}",
    )
    assert _ids(found) == ["FC-GQ-013"]
    assert found[0].confidence.value in ("medium", "low") and "observed" in found[0].message


# --- FC-GQ-014 -------------------------------------------------------------------------------


def test_reasoning_effort_per_model(tmp_path: Path) -> None:
    assert _chat(tmp_path, f"{M}, reasoning_effort='medium'") == []
    assert _chat(tmp_path, f"{M}, reasoning_effort=flag") == []
    # Llama: reasoning values are not documented, so nothing is claimed.
    found = _chat(
        tmp_path, "model='llama-3.1-8b-instant', messages=messages, reasoning_effort='none'"
    )
    assert _ids(found) == ["FC-GQ-006"]
    found = _chat(tmp_path, "model='qwen/qwen3.8-27b', messages=messages, reasoning_effort='none'")
    assert _ids(found) == ["FC-GQ-006"]  # preview note only: none is documented for Qwen


def test_raw_reasoning_with_tools_and_include_none(tmp_path: Path) -> None:
    q = "model='qwen/qwen3.8-27b', messages=messages"
    found = _chat(tmp_path, f"{q}, reasoning_format='raw', tools=[{{'type': 'function'}}]")
    assert sorted(_ids(found)) == ["FC-GQ-006", "FC-GQ-014"]
    gq014 = next(f for f in found if f.rule_id == "FC-GQ-014")
    assert "tool use" in gq014.message and "contract S8" in gq014.message
    assert _ids(_chat(tmp_path, f"{q}, reasoning_format='parsed', include_reasoning=None")) == [
        "FC-GQ-006"
    ]
    # json_schema is not "JSON mode" in S8: no 400 is claimed for it.
    assert "FC-GQ-014" not in _ids(_chat(tmp_path, f"{q}, reasoning_format='raw', {_rf(True)}"))


# --- FC-GQ-015 / 017 / 018 / 019 -------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "flagged"),
    [
        ("from openai import OpenAI\nc = OpenAI(base_url='https://api.groq.com/openai')\n", True),
        (
            "from openai import OpenAI\nc = OpenAI(base_url='https://api.groq.com/openai/v1')\n",
            False,
        ),
        ("import httpx\nc = httpx.Client(base_url='https://api.groq.com')\n", False),  # no posts
        (
            "from groq import AsyncGroq\nHOST = 'http://proxy:8080'\n"
            "c = AsyncGroq(base_url=f'{HOST}/openai/v1/')\n",
            True,
        ),
    ],
)
def test_base_url_cases(tmp_path: Path, source: str, flagged: bool) -> None:
    report = _scan(tmp_path, source)
    assert any(f.rule_id == "FC-GQ-015" for f in report.findings) is flagged


def test_responses_on_async_groq_and_not_on_openai(tmp_path: Path) -> None:
    source = (
        "from groq import AsyncGroq\nc = AsyncGroq(timeout=60.0)\n"
        "async def go():\n    async with c.responses.stream(model='openai/gpt-oss-20b') as s:\n"
        "        return s\n"
    )
    assert "FC-GQ-017" in [f.rule_id for f in _scan(tmp_path, source).findings]
    assert "FC-GQ-017" not in _ids(_responses(tmp_path, R))


def test_stop_and_tools_limits(tmp_path: Path) -> None:
    assert _chat(tmp_path, f"{M}, stop='END'") == []
    tools = ", ".join("{'type': 'function'}" for _ in range(128))
    assert _chat(tmp_path, f"{M}, tools=[{tools}]") == []
    found = _chat(tmp_path, f"{M}, tools=[{tools}, {{'type': 'function'}}]")
    assert _ids(found) == ["FC-GQ-018"] and found[0].confidence.value == "high"


def test_stream_options_on_a_stream_call_is_fine(tmp_path: Path) -> None:
    source = (
        f"{GROQ}def ask(messages):\n    return client.chat.completions.stream({M}, "
        "stream_options={'include_usage': True})\n"
    )
    assert not [f for f in _scan(tmp_path, source).findings if f.rule_id == "FC-GQ-019"]


# --- raw HTTP --------------------------------------------------------------------------------


def test_raw_post_to_groq_chat_is_checked(tmp_path: Path) -> None:
    source = (
        "import httpx\n"
        "def ask(messages):\n"
        "    return httpx.post('https://api.groq.com/openai/v1/chat/completions', "
        "json={'model': 'openai/gpt-oss-20b', 'messages': messages, 'n': 3})\n"
    )
    ids = [f.rule_id for f in _scan(tmp_path, source).findings]
    assert "FC-GQ-008" in ids


def test_report_json_round_trips_with_groq_findings(fixture_report: Report) -> None:
    data = json.loads(fixture_report.model_dump_json())
    assert Report.model_validate(data) == fixture_report
