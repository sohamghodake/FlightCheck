"""Groq configuration profile, dated catalogue and key redaction (phase G2, ADR-036)."""

from pathlib import Path

import pytest

from flightcheck.core.config import (
    GROQ_API_KEY_ENV,
    GROQ_BASE_URL,
    GROQ_DECOMMISSIONED_MODELS,
    GROQ_DEFAULT_MODEL,
    GROQ_DOCUMENTED_MODELS,
    GROQ_MODELS,
    load_config,
)
from flightcheck.core.errors import ConfigError
from flightcheck.core.redact import clear_registered_secrets, redact

NO_ENV: dict[str, str] = {}
# Fake key in Groq's format, never a complete literal in this file.
FAKE_GROQ_KEY = "gsk_" + "unitTestOnly0123456789abcdef"


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_registered_secrets()
    yield
    clear_registered_secrets()


def write_config(root: Path, text: str) -> None:
    (root / "flightcheck.toml").write_text(text, encoding="utf-8")


# --- the built-in profile --------------------------------------------------------------------


def test_builtin_groq_profile_has_the_contract_defaults(tmp_path: Path) -> None:
    config = load_config(tmp_path, environ=NO_ENV)
    groq = config.providers["groq"]
    assert (groq.name, groq.kind) == ("groq", "groq")
    assert groq.base_url == GROQ_BASE_URL == "https://api.groq.com/openai/v1"
    assert groq.api_key_env == GROQ_API_KEY_ENV == "GROQ_API_KEY"
    assert groq.default_model == GROQ_DEFAULT_MODEL == "openai/gpt-oss-20b"
    assert (groq.timeout.connect_s, groq.timeout.read_s) == (10.0, 60.0)
    assert (groq.retry.max_attempts, groq.retry.base_delay_s, groq.retry.max_delay_s) == (
        3,
        1.0,
        30.0,
    )
    assert groq.api_key is None
    # BharatCode stays the explain provider until G5 (ADR-035).
    assert config.explain.provider == "bharatcode"
    assert config.providers["bharatcode"].kind == "bharatcode"


@pytest.mark.parametrize(
    ("toml_text", "environ"),
    [
        ('[project]\nprovider = "groq"\n', NO_ENV),
        ("", {"FLIGHTCHECK_PROVIDER": "groq"}),
    ],
)
def test_groq_is_selectable_as_the_project_provider(
    tmp_path: Path, toml_text: str, environ: dict[str, str]
) -> None:
    write_config(tmp_path, toml_text)
    config = load_config(tmp_path, environ=environ)
    assert config.provider == "groq"
    assert config.providers[config.provider].kind == "groq"


def test_groq_profile_accepts_partial_overrides(tmp_path: Path) -> None:
    write_config(
        tmp_path,
        '[providers.groq]\ndefault_model = "qwen/qwen3.8-27b"\n[providers.groq.timeout]\n'
        "read_s = 90\n",
    )
    groq = load_config(tmp_path, environ=NO_ENV).providers["groq"]
    assert groq.default_model == "qwen/qwen3.8-27b"
    assert groq.timeout.read_s == 90.0
    assert groq.base_url == GROQ_BASE_URL  # untouched fields keep the built-in values


def test_groq_key_is_read_from_env_and_registered_for_redaction(tmp_path: Path) -> None:
    config = load_config(tmp_path, environ={"GROQ_API_KEY": FAKE_GROQ_KEY})
    secret = config.api_key_for("groq")
    assert secret is not None and secret.get_secret_value() == FAKE_GROQ_KEY
    assert FAKE_GROQ_KEY not in repr(config)
    assert FAKE_GROQ_KEY not in config.model_dump_json()
    # Registered as an exact value, which wins over the gsk_ pattern ("gsk_…[REDACTED]").
    assert redact(f"x{FAKE_GROQ_KEY}y", environ=NO_ENV) == "x[REDACTED]y"
    assert config.api_key_for("bharatcode") is None


def test_groq_key_in_flightcheck_toml_is_rejected(tmp_path: Path) -> None:
    write_config(tmp_path, f'[providers.groq]\napi_key = "{FAKE_GROQ_KEY}"\n')
    with pytest.raises(ConfigError, match="must not contain secrets") as info:
        load_config(tmp_path, environ=NO_ENV)
    assert FAKE_GROQ_KEY not in str(info.value)


# --- the dated catalogue (docs/GROQ_CONTRACT.md §1.8, 18 Nov 2026) ----------------------------


def test_catalogue_values_hand_checked_against_the_contract() -> None:
    # Spot checks copied by hand from the contract's model table.
    gpt20 = GROQ_MODELS["openai/gpt-oss-20b"]
    assert (gpt20.tier, gpt20.context_window, gpt20.max_completion_tokens) == (
        "production",
        131_072,
        65_536,
    )
    assert gpt20.strict_json_schema and gpt20.builtin_tools
    assert gpt20.parallel_tool_calls is False
    assert gpt20.reasoning_efforts == {"low", "medium", "high"}
    qwen = GROQ_MODELS["qwen/qwen3.8-27b"]
    assert (qwen.tier, qwen.max_completion_tokens) == ("preview", 16_384)
    assert "none" in qwen.reasoning_efforts and qwen.parallel_tool_calls is True
    llama = GROQ_MODELS["llama-3.3-70b-versatile"]
    assert (llama.enterprise_only, llama.max_completion_tokens) == (True, 32_768)
    assert not llama.json_schema
    assert GROQ_MODELS["minimaxai/minimax-m2.7"].context_window == 196_608
    assert GROQ_MODELS["openai/gpt-oss-safeguard-20b"].strict_json_schema is False
    assert GROQ_MODELS["whisper-large-v3"].kind == "audio"


def test_catalogue_is_consistent() -> None:
    assert GROQ_DOCUMENTED_MODELS == set(GROQ_MODELS)
    assert not GROQ_DOCUMENTED_MODELS & GROQ_DECOMMISSIONED_MODELS
    assert GROQ_DEFAULT_MODEL in GROQ_MODELS
    default = GROQ_MODELS[GROQ_DEFAULT_MODEL]
    assert default.tier == "production" and default.strict_json_schema  # contract §3
    for model_id, facts in GROQ_MODELS.items():
        assert facts.tier in ("production", "preview"), model_id
        assert facts.kind in ("chat", "classifier", "audio", "tts"), model_id
        if facts.strict_json_schema:
            assert facts.json_schema, model_id  # strict models are best-effort models too
    assert {"llama3-70b-8192", "groq/compound", "qwen/qwen3.6-27b"} <= GROQ_DECOMMISSIONED_MODELS


# --- gsk_ redaction (core.redact, shared path) -------------------------------------------------


def test_groq_key_is_redacted_by_pattern() -> None:
    out = redact(f"GROQ_API_KEY={FAKE_GROQ_KEY} and Bearer-less use", environ=NO_ENV)
    assert FAKE_GROQ_KEY not in out
    assert FAKE_GROQ_KEY[4:] not in out
    assert "gsk_…[REDACTED]" in out or "GROQ_API_KEY=[REDACTED]" in out


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("key gsk_" + "abcdEFGH1234 here", "key gsk_…[REDACTED] here"),
        ('{"k": "gsk_' + 'abcdEFGH1234"}', '{"k": "gsk_…[REDACTED]"}'),
        ("gsk_" + "your_secret_key_here", "gsk_…[REDACTED]"),  # Groq's own placeholder
        ("gsk_" + "short", "gsk_short"),  # under 8 key characters: left alone
        ("tasks_and_disks are fine", "tasks_and_disks are fine"),
    ],
)
def test_groq_pattern_cases(text: str, expected: str) -> None:
    assert redact(text, environ=NO_ENV) == expected


def test_groq_redaction_is_idempotent() -> None:
    once = redact("x gsk_" + "abcdEFGH1234", environ=NO_ENV)
    assert redact(once, environ=NO_ENV) == once
