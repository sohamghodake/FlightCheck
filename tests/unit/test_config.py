from pathlib import Path

import pytest

from flightcheck.core.config import (
    BHARATCODE_BASE_URL,
    FlightCheckConfig,
    RetryPolicy,
    load_config,
)
from flightcheck.core.errors import ConfigError, ErrorKind
from flightcheck.core.redact import clear_registered_secrets, redact

NO_ENV: dict[str, str] = {}


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_registered_secrets()
    yield
    clear_registered_secrets()


def write_config(root: Path, text: str) -> None:
    (root / "flightcheck.toml").write_text(text, encoding="utf-8")


def test_missing_file_gives_documented_defaults(tmp_path: Path) -> None:
    config = load_config(tmp_path, environ=NO_ENV)
    bharatcode = config.providers["bharatcode"]
    assert bharatcode.base_url == BHARATCODE_BASE_URL
    assert bharatcode.api_key_env == "BHARATCODE_API_KEY"
    assert bharatcode.default_model == "qwen-3.8-27b"
    assert bharatcode.timeout.connect_s == 10 and bharatcode.timeout.read_s == 300
    assert bharatcode.retry.max_attempts == 3
    assert bharatcode.api_key is None
    assert config.test is None
    assert config.explain.max_output_tokens == 4000
    assert config.scan.max_files == 5000


def test_precedence_default_file_env_override(tmp_path: Path) -> None:
    write_config(
        tmp_path,
        '[explain]\nmodel = "file-model"\nllm = "fake"\ncontext = "snippets"\n',
    )
    env = {"FLIGHTCHECK_MODEL": "env-model", "FLIGHTCHECK_LLM": "none"}
    config = load_config(tmp_path, {"explain": {"model": "cli-model"}}, environ=env)
    assert config.explain.model == "cli-model"  # override beats env and file
    assert config.explain.llm == "none"  # env beats file
    assert config.explain.context == "snippets"  # file beats default
    assert config.explain.max_groups == 5  # default


def test_none_overrides_are_ignored(tmp_path: Path) -> None:
    write_config(tmp_path, '[explain]\nmodel = "file-model"\n')
    config = load_config(tmp_path, {"explain": {"model": None}}, environ=NO_ENV)
    assert config.explain.model == "file-model"


def test_project_provider_and_partial_provider_override(tmp_path: Path) -> None:
    write_config(
        tmp_path,
        '[project]\nprovider = "bharatcode"\n\n[providers.bharatcode.timeout]\nread_s = 120\n',
    )
    config = load_config(tmp_path, environ=NO_ENV)
    assert config.provider == "bharatcode"
    assert config.providers["bharatcode"].timeout.read_s == 120
    assert config.providers["bharatcode"].timeout.connect_s == 10
    assert config.providers["bharatcode"].base_url == BHARATCODE_BASE_URL


@pytest.mark.parametrize(
    "toml_text",
    [
        'api_key = "bc_live_abcdef123456"\n',
        '[providers.bharatcode]\napi_key = "bc_live_abcdef123456"\n',
        '[explain]\ntoken = "abcdefgh"\n',
        '[test]\ncommand = ["python", "app.py"]\nsecret = "x"\n',
    ],
)
def test_rejects_secret_looking_keys_with_values(tmp_path: Path, toml_text: str) -> None:
    write_config(tmp_path, toml_text)
    with pytest.raises(ConfigError) as info:
        load_config(tmp_path, environ=NO_ENV)
    assert info.value.exit_code == 2
    assert "must not contain secrets" in info.value.message
    assert "bc_live_abcdef123456" not in info.value.message
    assert info.value.hint and "BHARATCODE_API_KEY" in info.value.hint


def test_env_name_keys_and_token_limits_are_not_secrets(tmp_path: Path) -> None:
    write_config(
        tmp_path,
        '[providers.bharatcode]\napi_key_env = "MY_BC_KEY"\n\n'
        "[explain]\nmax_output_tokens = 8000\n\n"
        '[test]\ncommand = ["python", "app.py"]\napi_key_env = ["MY_BC_KEY"]\n',
    )
    config = load_config(tmp_path, environ=NO_ENV)
    assert config.providers["bharatcode"].api_key_env == "MY_BC_KEY"
    assert config.explain.max_output_tokens == 8000
    assert config.test is not None and config.test.api_key_env == ["MY_BC_KEY"]


def test_api_key_comes_from_env_as_secretstr(tmp_path: Path) -> None:
    key = "bc_live_realkey987654"
    config = load_config(tmp_path, environ={"BHARATCODE_API_KEY": key})
    secret = config.api_key_for("bharatcode")
    assert secret is not None and secret.get_secret_value() == key
    assert key not in repr(config)
    assert key not in config.model_dump_json()
    assert "api_key" not in config.model_dump()["providers"]["bharatcode"]
    # The real key is registered for redaction even where no pattern would match it.
    assert redact(f"value={key[8:]}{key}", environ=NO_ENV).count("[REDACTED]") >= 1


def test_api_key_env_name_is_respected(tmp_path: Path) -> None:
    write_config(tmp_path, '[providers.bharatcode]\napi_key_env = "MY_BC_KEY"\n')
    config = load_config(
        tmp_path, environ={"MY_BC_KEY": "abc12345678", "BHARATCODE_API_KEY": "other123456"}
    )
    secret = config.api_key_for("bharatcode")
    assert secret is not None and secret.get_secret_value() == "abc12345678"


def test_empty_api_key_env_value_means_no_key(tmp_path: Path) -> None:
    config = load_config(tmp_path, environ={"BHARATCODE_API_KEY": ""})
    assert config.api_key_for("bharatcode") is None


def test_test_command_must_be_a_list(tmp_path: Path) -> None:
    write_config(tmp_path, '[test]\ncommand = "python app.py"\n')
    with pytest.raises(ConfigError, match="must be a list of strings"):
        load_config(tmp_path, environ=NO_ENV)


def test_test_command_items_must_be_non_empty_strings(tmp_path: Path) -> None:
    write_config(tmp_path, '[test]\ncommand = ["python", ""]\n')
    with pytest.raises(ConfigError, match="test.command"):
        load_config(tmp_path, environ=NO_ENV)


def test_test_section_defaults(tmp_path: Path) -> None:
    write_config(tmp_path, '[test]\ncommand = ["python", "app.py"]\n')
    config = load_config(tmp_path, environ=NO_ENV)
    assert config.test is not None
    assert config.test.timeout_s == 60
    assert config.test.max_requests == 25
    assert config.test.scenarios == ["S01", "S02", "S03", "S04", "S05", "S06"]
    assert config.test.base_url_env == ["BHARATCODE_BASE_URL", "OPENAI_BASE_URL"]


def test_test_expectations_are_validated(tmp_path: Path) -> None:
    write_config(
        tmp_path,
        '[test]\ncommand = ["python", "app.py"]\n\n[test.expect]\non_truncation = "explode"\n',
    )
    with pytest.raises(ConfigError, match="on_truncation"):
        load_config(tmp_path, environ=NO_ENV)


def test_invalid_toml_reports_line(tmp_path: Path) -> None:
    write_config(tmp_path, '[explain]\nmodel = "unterminated\n')
    with pytest.raises(ConfigError) as info:
        load_config(tmp_path, environ=NO_ENV)
    assert "Invalid TOML" in info.value.message
    assert "line 2" in info.value.message


def test_unknown_keys_are_rejected(tmp_path: Path) -> None:
    write_config(tmp_path, "[scan]\nmax_filez = 3\n")
    with pytest.raises(ConfigError, match="scan.max_filez"):
        load_config(tmp_path, environ=NO_ENV)


def test_unknown_project_key_is_rejected(tmp_path: Path) -> None:
    write_config(tmp_path, '[project]\nname = "x"\n')
    with pytest.raises(ConfigError, match=r"\[project\]"):
        load_config(tmp_path, environ=NO_ENV)


def test_max_output_tokens_is_capped(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="max_output_tokens"):
        load_config(tmp_path, {"explain": {"max_output_tokens": 32_001}}, environ=NO_ENV)


def test_override_secrets_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="must not contain secrets"):
        load_config(tmp_path, {"providers": {"bharatcode": {"api_key": "x"}}}, environ=NO_ENV)


def test_explain_provider_must_exist(tmp_path: Path) -> None:
    write_config(tmp_path, '[explain]\nprovider = "nope"\n')
    with pytest.raises(ConfigError, match="not defined"):
        load_config(tmp_path, environ=NO_ENV)


def test_root_may_be_a_file(tmp_path: Path) -> None:
    write_config(tmp_path, '[explain]\nmodel = "m"\n')
    app = tmp_path / "app.py"
    app.write_text("", encoding="utf-8")
    assert load_config(app, environ=NO_ENV).explain.model == "m"


def test_unrelated_flightcheck_env_vars_are_ignored(tmp_path: Path) -> None:
    config = load_config(tmp_path, environ={"FLIGHTCHECK_NO_NETWORK": "1"})
    assert isinstance(config, FlightCheckConfig)


def test_retry_policy_defaults_and_delay() -> None:
    policy = RetryPolicy()
    assert policy.retry_on == {ErrorKind.BUSY, ErrorKind.RATE_LIMITED, ErrorKind.CONNECTION}
    assert [policy.delay_for(n) for n in (1, 2, 3, 4, 5, 6)] == [2, 4, 8, 16, 20, 20]
    assert policy.delay_for(1, jitter=1.2) == pytest.approx(2.4)


def test_retry_policy_rejects_max_below_base() -> None:
    with pytest.raises(ValueError, match="max_delay_s"):
        RetryPolicy(base_delay_s=5, max_delay_s=1)
