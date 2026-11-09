import time

import pytest

from flightcheck.core.redact import (
    clear_registered_secrets,
    redact,
    redact_obj,
    register_secret,
)

NO_ENV: dict[str, str] = {}


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_registered_secrets()
    yield
    clear_registered_secrets()


def test_bharatcode_live_key_is_redacted() -> None:
    out = redact("key=bc_live_abcdef123456", environ=NO_ENV)
    assert out == "key=bc_live_…[REDACTED]"
    assert "abcdef123456" not in out


def test_sk_key_is_redacted() -> None:
    out = redact("OPENAI key sk-proj_ABCdef123456789 here", environ=NO_ENV)
    assert out == "OPENAI key sk-…[REDACTED] here"


def test_bearer_token_is_redacted_case_insensitive() -> None:
    out = redact("Authorization: bearer abc.DEF-123_456xyz", environ=NO_ENV)
    assert out == "Authorization: Bearer [REDACTED]"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("api_key=supersecretvalue", "api_key=[REDACTED]"),
        ("API-KEY: 'supersecretvalue'", "API-KEY=[REDACTED]'"),
        ('secret = "hunter2hunter2"', 'secret=[REDACTED]"'),
        ("token:abcdefgh12", "token=[REDACTED]"),
        ("password=correcthorsebattery", "password=[REDACTED]"),
    ],
)
def test_assignment_pattern_is_redacted(text: str, expected: str) -> None:
    out = redact(text, environ=NO_ENV)
    assert out == expected


def test_env_value_redaction_uses_variable_name() -> None:
    env = {"MY_SERVICE_TOKEN": "tok-value-1234567", "HOME": "/home/someone-long"}
    out = redact("using tok-value-1234567 from /home/someone-long", environ=env)
    assert out == "using [REDACTED:MY_SERVICE_TOKEN] from /home/someone-long"


def test_env_values_shorter_than_eight_chars_are_not_redacted() -> None:
    env = {"SOME_KEY": "short"}
    assert redact("a short note", environ=env) == "a short note"


def test_env_redaction_reads_process_environment_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FC_TEST_SECRET", "zz-planted-value-99")
    assert redact("leak: zz-planted-value-99") == "leak: [REDACTED:FC_TEST_SECRET]"


def test_registered_secret_is_redacted_exactly() -> None:
    register_secret("plain-looking-value-42")
    assert redact("x plain-looking-value-42 y", environ=NO_ENV) == "x [REDACTED] y"


def test_register_secret_ignores_tiny_and_empty_values() -> None:
    register_secret("")
    register_secret(None)
    register_secret("abc")
    assert redact("abc abc", environ=NO_ENV) == "abc abc"


@pytest.mark.parametrize(
    "prose",
    [
        "The token budget for explain is 4000 tokens; max_output_tokens=4000.",
        "Set the secret in your shell profile, not in flightcheck.toml.",
        "Use a bearer token in the Authorization header.",
        "sk-short and bc_live_ are prefixes only.",
        "",
    ],
)
def test_normal_prose_is_unchanged(prose: str) -> None:
    assert redact(prose, environ=NO_ENV) == prose


def test_redaction_is_idempotent() -> None:
    once = redact("api_key=supersecretvalue bc_live_abcdef123456", environ=NO_ENV)
    assert redact(once, environ=NO_ENV) == once


def test_redact_obj_is_deep_and_returns_copies() -> None:
    original = {
        "headers": {"Authorization": "Bearer abcdefghijkl"},
        "items": ["bc_live_secret1234", ("sk-abcdefghijklmnop", 7)],
        "count": 3,
        "none": None,
    }
    out = redact_obj(original, environ=NO_ENV)
    assert out == {
        "headers": {"Authorization": "Bearer [REDACTED]"},
        "items": ["bc_live_…[REDACTED]", ("sk-…[REDACTED]", 7)],
        "count": 3,
        "none": None,
    }
    assert original["headers"]["Authorization"] == "Bearer abcdefghijkl"
    assert out["headers"] is not original["headers"]
    assert out["items"] is not original["items"]


def test_very_long_text_is_fast() -> None:
    text = ("lorem ipsum dolor sit amet " * 40_000) + "bc_live_abcdef123456"
    start = time.perf_counter()
    out = redact(text, environ=NO_ENV)
    assert time.perf_counter() - start < 2.0
    assert out.endswith("bc_live_…[REDACTED]")


def test_key_value_pattern_keeps_json_text_valid_phase8():
    """Phase 8 fix: redacting JSON text that holds an already-redacted ``api_key=...`` value
    must not eat the backslash of the escaped closing quote (it broke the explain payload)."""
    import json

    once = redact('client = OpenAI(api_key="bc_live_abcdefgh12")', environ=NO_ENV)
    text = json.dumps({"snippet": once})
    assert json.loads(redact(text, environ=NO_ENV)) == {"snippet": once}
    # A backslash inside a value is still part of it; only an escaped quote ends it.
    assert redact(r"password=abc\def12345", environ=NO_ENV) == "password=[REDACTED]"
