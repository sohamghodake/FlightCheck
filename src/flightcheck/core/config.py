"""Configuration: defaults < flightcheck.toml < FLIGHTCHECK_* environment < CLI overrides.

``flightcheck.toml`` can never contain secrets. API keys are read only from the environment
variable named by ``providers.<name>.api_key_env`` and held as ``SecretStr``.

TOML layout::

    [project]
    provider = "bharatcode"        # optional; overrides provider detection ("groq" too)

    [providers.bharatcode]         # optional; partial overrides of the built-in profile
    default_model = "qwen-3.8-27b"
    [providers.bharatcode.timeout]
    read_s = 300

    [scan]
    exclude = ["examples/**"]

    [test]
    command = ["python", "app.py"]

    [explain]
    llm = "none"
"""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)

from flightcheck.core.capabilities import Capability
from flightcheck.core.errors import ConfigError, ErrorKind
from flightcheck.core.redact import register_secret

CONFIG_FILENAME = "flightcheck.toml"

# Documented BharatCode defaults (docs/BHARATCODE_CONTRACT.md §15.3).
BHARATCODE_BASE_URL = "https://bharatcode.ai/api/model/v1"
BHARATCODE_API_KEY_ENV = "BHARATCODE_API_KEY"
BHARATCODE_DEFAULT_MODEL = "qwen-3.8-27b"
MAX_OUTPUT_TOKENS_CAP = 32_000

# Documented contract facts shared by the scanner rules and the mock (BHARATCODE_CONTRACT.md
# §15.1). They live in core so that neither package has to import the other.
BHARATCODE_DOCUMENTED_MODELS = frozenset({"qwen-3.8-27b", "deepseek-v4.1-flash"})
BHARATCODE_PRO_ONLY_MODEL = "deepseek-v4.1-flash"
# Audio/video/file input is documented as unsupported on /responses; the exact rejected
# type names are [UNVERIFIED] (OpenAI's names are used).
UNSUPPORTED_INPUT_TYPES = frozenset({"input_audio", "input_file"})
FILE_INPUT_KEYS = ("file_id", "file_data")

# Documented Groq defaults and facts (docs/GROQ_CONTRACT.md, read 18 Nov 2026; ADR-035/036).
GROQ_BASE_URL = "https://api.groq.com/openai/v1"  # contract A1 (OpenAI-compatible base URL)
GROQ_HOST = "api.groq.com"
GROQ_API_KEY_ENV = "GROQ_API_KEY"  # contract A3
GROQ_BASE_URL_ENV = "GROQ_BASE_URL"  # read by the groq SDK (contract K3)
GROQ_DEFAULT_MODEL = "openai/gpt-oss-20b"  # contract §3
GROQ_CATALOGUE_DATE = "18 Nov 2026"  # the model tables below are a dated snapshot


@dataclass(frozen=True)
class GroqModelFacts:
    """One row of the Groq model catalogue (contract §1.8). ``None`` means not documented."""

    tier: str  # "production" | "preview"
    kind: str  # "chat" | "classifier" | "audio" | "tts"
    context_window: int | None
    max_completion_tokens: int | None
    enterprise_only: bool = False
    strict_json_schema: bool = False  # response_format json_schema with strict: true (S3)
    json_schema: bool = False  # json_schema best effort (S3)
    parallel_tool_calls: bool | None = None  # contract T7
    builtin_tools: bool = False  # browser_search / code_interpreter (R9, T8)
    reasoning_efforts: frozenset[str] | None = None  # accepted reasoning_effort values (M4)
    # False: reasoning_format is documented as not supported (M5, §1.8; GPT-OSS). ADR-037.
    reasoning_format: bool | None = None


_GPT_OSS_EFFORTS = frozenset({"low", "medium", "high"})
GROQ_MODELS: dict[str, GroqModelFacts] = {
    "openai/gpt-oss-120b": GroqModelFacts(
        "production",
        "chat",
        131_072,
        65_536,
        strict_json_schema=True,
        json_schema=True,
        parallel_tool_calls=False,
        builtin_tools=True,
        reasoning_efforts=_GPT_OSS_EFFORTS,
        reasoning_format=False,
    ),
    "openai/gpt-oss-20b": GroqModelFacts(
        "production",
        "chat",
        131_072,
        65_536,
        strict_json_schema=True,
        json_schema=True,
        parallel_tool_calls=False,
        builtin_tools=True,
        reasoning_efforts=_GPT_OSS_EFFORTS,
        reasoning_format=False,
    ),
    "llama-3.3-70b-versatile": GroqModelFacts(
        "production", "chat", 131_072, 32_768, enterprise_only=True, parallel_tool_calls=True
    ),
    "llama-3.1-8b-instant": GroqModelFacts(
        "production", "chat", 131_072, 131_072, enterprise_only=True, parallel_tool_calls=True
    ),
    "qwen/qwen3.8-27b": GroqModelFacts(
        "preview",
        "chat",
        131_072,
        16_384,
        strict_json_schema=True,
        json_schema=True,
        parallel_tool_calls=True,
        reasoning_efforts=frozenset({"none", "default", "low", "medium", "high"}),
    ),
    "openai/gpt-oss-safeguard-20b": GroqModelFacts(
        "preview", "chat", 131_072, 65_536, json_schema=True, parallel_tool_calls=False
    ),
    "meta-models/muse-spark-open": GroqModelFacts(
        "preview",
        "chat",
        131_072,
        131_072,
        enterprise_only=True,
        strict_json_schema=True,
        json_schema=True,
        parallel_tool_calls=True,
        reasoning_efforts=frozenset({"low", "medium", "high", "xhigh"}),
    ),
    "minimaxai/minimax-m2.7": GroqModelFacts(
        "preview", "chat", 196_608, 131_072, enterprise_only=True, parallel_tool_calls=True
    ),
    "meta-llama/llama-prompt-guard-2-22m": GroqModelFacts("preview", "classifier", 512, 512),
    "meta-llama/llama-prompt-guard-2-86m": GroqModelFacts("preview", "classifier", 512, 512),
    "whisper-large-v3": GroqModelFacts("production", "audio", None, None),
    "whisper-large-v3-turbo": GroqModelFacts("production", "audio", None, None),
    "canopylabs/orpheus-v1-english": GroqModelFacts("preview", "tts", 4_000, 50_000),
    "canopylabs/orpheus-arabic-saudi": GroqModelFacts("preview", "tts", 4_000, 50_000),
}
GROQ_DOCUMENTED_MODELS = frozenset(GROQ_MODELS)

# `flightcheck test` on a Groq run (ADR-038): the [test] defaults used when the developer did not
# set the field. GROQ_BASE_URL is the groq SDK's variable and gets the mock **root** (the SDK
# adds /openai/v1 itself, contract K3/K4); every other base-URL variable gets <mock>/openai/v1
# (the OpenAI SDK needs the full prefix, K4). S11 (429 with retry-after) runs on Groq only.
GROQ_TEST_BASE_URL_ENV = (GROQ_BASE_URL_ENV, "OPENAI_BASE_URL")
GROQ_TEST_API_KEY_ENV = (GROQ_API_KEY_ENV, "OPENAI_API_KEY")
GROQ_TEST_SCENARIOS = ("S01", "S02", "S03", "S04", "S05", "S06", "S11")
RuntimeProvider = Literal["bharatcode", "groq"]
# Shut down for free/developer usage (contract M2): requests to these IDs fail. The two Llama
# models shut down on 16 Aug 2026 for free/developer tiers stay in GROQ_MODELS (Enterprise).
GROQ_DECOMMISSIONED_MODELS = frozenset(
    {
        "groq/compound",
        "groq/compound-mini",
        "qwen/qwen3.6-27b",
        "qwen/qwen3-32b",
        "meta-llama/llama-4-scout-17b-16e-instruct",
        "meta-llama/llama-4-maverick-17b-128e-instruct",
        "meta-llama/llama-guard-4-12b",
        "moonshotai/kimi-k2-instruct-0905",
        "moonshotai/kimi-k2-instruct",
        "playai-tts",
        "playai-tts-arabic",
        "gemma2-9b-it",
        "gemma-7b-it",
        "deepseek-r1-distill-llama-70b",
        "deepseek-r1-distill-llama-70b-specdec",
        "deepseek-r1-distill-qwen-32b",
        "llama3-70b-8192",
        "llama3-8b-8192",
        "llama3-groq-8b-8192-tool-use-preview",
        "llama3-groq-70b-8192-tool-use-preview",
        "llama-guard-3-8b",
        "llama-3.1-70b-versatile",
        "llama-3.1-70b-specdec",
        "llama-3.3-70b-specdec",
        "llama-3.2-1b-preview",
        "llama-3.2-3b-preview",
        "llama-3.2-11b-vision-preview",
        "llama-3.2-90b-vision-preview",
        "llama-3.2-11b-text-preview",
        "llama-3.2-90b-text-preview",
        "llava-v1.5-7b-4096-preview",
        "distil-whisper-large-v3-en",
        "mistral-saba-24b",
        "mixtral-8x7b-32768",
        "qwen-qwq-32b",
        "qwen-2.5-32b",
        "qwen-2.5-coder-32b",
    }
)

MIB = 1024 * 1024

# Keys whose names look like they hold a secret. Keys ending in "_env" name an environment
# variable rather than holding a value, so they are allowed (e.g. api_key_env).
_SECRET_KEY_NAME = re.compile(r"(?i)(api_?key|token|secret|password)")

# Environment variables that map onto config fields (others named FLIGHTCHECK_* are ignored
# here; FLIGHTCHECK_NO_NETWORK and FLIGHTCHECK_LIVE are runtime switches, not config).
ENV_OVERRIDES: dict[str, tuple[str, ...]] = {
    "FLIGHTCHECK_PROVIDER": ("provider",),
    "FLIGHTCHECK_LLM": ("explain", "llm"),
    "FLIGHTCHECK_MODEL": ("explain", "model"),
    "FLIGHTCHECK_CONTEXT": ("explain", "context"),
}

_EXPECT_PATTERN = re.compile(r"^(nonzero_exit|any|stdout_contains:.+|stderr_contains:.+)$")


class _Config(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TimeoutConfig(_Config):
    """HTTP timeouts in seconds for live provider calls (§15.3 defaults)."""

    connect_s: float = Field(default=10.0, gt=0)
    read_s: float = Field(default=300.0, gt=0)
    write_s: float = Field(default=30.0, gt=0)
    pool_s: float = Field(default=10.0, gt=0)


class RetryPolicy(_Config):
    """Bounded retries with exponential backoff (executed by providers/http.py)."""

    max_attempts: int = Field(default=3, ge=1, le=10)  # total, including the first
    base_delay_s: float = Field(default=2.0, ge=0)  # BharatCode: "wait a few seconds"
    max_delay_s: float = Field(default=20.0, ge=0)
    retry_on: frozenset[ErrorKind] = frozenset(
        {ErrorKind.BUSY, ErrorKind.RATE_LIMITED, ErrorKind.CONNECTION}
    )

    @model_validator(mode="after")
    def _max_not_below_base(self) -> RetryPolicy:
        if self.max_delay_s < self.base_delay_s:
            raise ValueError("max_delay_s must be >= base_delay_s")
        return self

    def delay_for(self, attempt: int, jitter: float = 1.0) -> float:
        """Delay before retry number ``attempt`` (1-based); ``jitter`` is uniform(0.8, 1.2)."""
        return min(self.max_delay_s, self.base_delay_s * 2 ** (attempt - 1)) * jitter


class ProviderConfig(_Config):
    name: str
    kind: Literal["bharatcode", "groq", "openai_compatible"]
    base_url: str
    api_key_env: str = BHARATCODE_API_KEY_ENV
    default_model: str = BHARATCODE_DEFAULT_MODEL
    timeout: TimeoutConfig = Field(default_factory=TimeoutConfig)
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    # [providers.<name>.declared_capabilities] in flightcheck.toml: True → DECLARED, False →
    # DECLARED_UNSUPPORTED (providers/capabilities.py). Unknown capability names are errors.
    declared_capabilities: dict[Capability, bool] = Field(default_factory=dict)
    # Filled by load_config from the environment only. Never serialised.
    api_key: SecretStr | None = Field(default=None, exclude=True, repr=False)

    @field_validator("base_url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        return value.rstrip("/")


def _default_providers() -> dict[str, ProviderConfig]:
    return {
        "bharatcode": ProviderConfig(
            name="bharatcode", kind="bharatcode", base_url=BHARATCODE_BASE_URL
        ),
        # Groq (ADR-036): contract §3 defaults. Timeouts follow the groq SDK's 60 s read
        # default rather than BharatCode's queue-sized 300 s; retries wait 1 s, then double
        # (capped at 30 s). No adapter uses this profile until G5.
        "groq": ProviderConfig(
            name="groq",
            kind="groq",
            base_url=GROQ_BASE_URL,
            api_key_env=GROQ_API_KEY_ENV,
            default_model=GROQ_DEFAULT_MODEL,
            timeout=TimeoutConfig(read_s=60.0),
            retry=RetryPolicy(base_delay_s=1.0, max_delay_s=30.0),
        ),
    }


class ScanConfig(_Config):
    """Discovery exclusions and limits (§20.3)."""

    exclude: list[str] = Field(default_factory=list)  # extra glob patterns
    max_file_bytes: int = Field(default=1 * MIB, ge=1)
    max_files: int = Field(default=5000, ge=1)
    max_total_bytes: int = Field(default=50 * MIB, ge=1)


class ExpectConfig(_Config):
    """What the app should do in failure scenarios. Without it, outcomes are needs_review."""

    on_truncation: str | None = None
    on_stream_failure: str | None = None
    on_busy_exhausted: str | None = None

    @field_validator("on_truncation", "on_stream_failure", "on_busy_exhausted")
    @classmethod
    def _valid_expectation(cls, value: str | None) -> str | None:
        if value is not None and not _EXPECT_PATTERN.match(value):
            raise ValueError(
                'must be "nonzero_exit", "any", "stdout_contains:<text>" or '
                '"stderr_contains:<text>"'
            )
        return value


class TestConfig(_Config):
    """The [test] section: how to run the developer's app against the mock (§17.2)."""

    __test__ = False  # not a pytest test class

    command: list[str] = Field(min_length=1)
    cwd: str = "."
    timeout_s: int = Field(default=60, ge=1, le=600)
    base_url_env: list[str] = Field(
        default_factory=lambda: ["BHARATCODE_BASE_URL", "OPENAI_BASE_URL"]
    )
    api_key_env: list[str] = Field(default_factory=lambda: ["BHARATCODE_API_KEY", "OPENAI_API_KEY"])
    scenarios: list[str] = Field(default_factory=lambda: ["S01", "S02", "S03", "S04", "S05", "S06"])
    stdin: str = ""
    max_requests: int = Field(default=25, ge=1)
    expect: ExpectConfig | None = None

    def for_provider(self, provider: RuntimeProvider) -> TestConfig:
        """This section with the run provider's defaults for fields the developer did not set
        (ADR-038). BharatCode runs get ``self`` unchanged; Groq runs get the Groq variable names
        and scenario list (``GROQ_TEST_*``) wherever ``base_url_env``, ``api_key_env`` or
        ``scenarios`` was left at its default. A field set in ``flightcheck.toml`` or an
        override always wins."""
        if provider != "groq":
            return self
        defaults = {
            "base_url_env": list(GROQ_TEST_BASE_URL_ENV),
            "api_key_env": list(GROQ_TEST_API_KEY_ENV),
            "scenarios": list(GROQ_TEST_SCENARIOS),
        }
        update = {k: v for k, v in defaults.items() if k not in self.model_fields_set}
        return self.model_copy(update=update) if update else self

    @field_validator("command", mode="before")
    @classmethod
    def _command_is_list(cls, value: Any) -> Any:
        if isinstance(value, str):
            raise ValueError(
                'must be a list of strings, e.g. command = ["python", "app.py"] '
                "(a single string is not split, to avoid shell parsing)"
            )
        return value

    @field_validator("command")
    @classmethod
    def _command_items_not_empty(cls, value: list[str]) -> list[str]:
        if any(not part for part in value):
            raise ValueError("must not contain empty strings")
        return value


class ExplainConfig(_Config):
    llm: Literal["none", "fake", "bharatcode"] | None = None  # None: bharatcode if key set
    provider: str = "bharatcode"
    model: str | None = None  # None: the provider's default_model
    context: Literal["metadata", "snippets"] = "metadata"
    max_groups: int = Field(default=5, ge=1)
    max_calls: int = Field(default=3, ge=1)
    max_output_tokens: int = Field(default=4000, ge=1, le=MAX_OUTPUT_TOKENS_CAP)
    reasoning_none: bool = True  # send reasoning: {"effort": "none"}


class FlightCheckConfig(_Config):
    provider: str | None = None  # [project] provider: overrides detection
    providers: dict[str, ProviderConfig] = Field(default_factory=_default_providers)
    scan: ScanConfig = Field(default_factory=ScanConfig)
    test: TestConfig | None = None
    explain: ExplainConfig = Field(default_factory=ExplainConfig)

    @model_validator(mode="after")
    def _explain_provider_exists(self) -> FlightCheckConfig:
        if self.explain.provider not in self.providers:
            raise ValueError(
                f"explain.provider '{self.explain.provider}' is not defined under [providers]"
            )
        return self

    def api_key_for(self, provider_name: str) -> SecretStr | None:
        provider = self.providers.get(provider_name)
        return provider.api_key if provider else None


# --- Loading --------------------------------------------------------------------------------


def _reject_secrets(data: Any, source: str, path: tuple[str, ...] = ()) -> None:
    if isinstance(data, dict):
        for key, value in data.items():
            key_path = (*path, str(key))
            name = str(key)
            looks_secret = _SECRET_KEY_NAME.search(name) and not name.lower().endswith("_env")
            if looks_secret and isinstance(value, str) and value:
                # Never include the value itself in the message.
                raise ConfigError(
                    f"{source} must not contain secrets: '{'.'.join(key_path)}' has a value.",
                    hint=(
                        "Remove it and export the key in the environment variable named by "
                        f"api_key_env (default {BHARATCODE_API_KEY_ENV}) instead."
                    ),
                )
            _reject_secrets(value, source, key_path)
    elif isinstance(data, list):
        for index, item in enumerate(data):
            _reject_secrets(item, source, (*path, str(index)))


def _deep_merge(base: dict[str, Any], extra: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in extra.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _drop_none(data: Mapping[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in data.items():
        if value is None:
            continue
        cleaned[key] = _drop_none(value) if isinstance(value, Mapping) else value
    return cleaned


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError(f"{CONFIG_FILENAME} is not valid UTF-8: {exc.reason}") from exc
    except OSError as exc:
        raise ConfigError(f"Cannot read {CONFIG_FILENAME}: {exc.strerror}") from exc
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(
            f"Invalid TOML in {CONFIG_FILENAME}: {exc}",
            hint="Fix the syntax at the line and column shown.",
        ) from exc


def _normalise_file_data(data: dict[str, Any]) -> dict[str, Any]:
    data = dict(data)
    project = data.pop("project", None)
    if project is not None:
        if not isinstance(project, dict) or set(project) - {"provider"}:
            raise ConfigError(
                f"[project] in {CONFIG_FILENAME} supports only the 'provider' key.",
            )
        if "provider" in project:
            data["provider"] = project["provider"]
    providers = data.get("providers")
    if isinstance(providers, dict):
        for name, provider in providers.items():
            if isinstance(provider, dict):
                provider.setdefault("name", name)
    return data


def _env_data(environ: Mapping[str, str]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for env_name, field_path in ENV_OVERRIDES.items():
        value = environ.get(env_name)
        if not value:
            continue
        node = data
        for part in field_path[:-1]:
            node = node.setdefault(part, {})
        node[field_path[-1]] = value
    return data


def _format_validation_error(exc: ValidationError) -> str:
    # Use only the location and message: the input value could be sensitive.
    lines = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "(root)"
        lines.append(f"{location}: {error['msg']}")
    return "; ".join(lines)


def load_config(
    root: str | os.PathLike[str],
    overrides: Mapping[str, Any] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> FlightCheckConfig:
    """Load configuration for the project at ``root``.

    ``overrides`` is a nested mapping from CLI flags (``None`` values are ignored), e.g.
    ``{"explain": {"llm": "none"}}``. Raises :class:`ConfigError` on any problem.
    """
    env = os.environ if environ is None else environ
    root_path = Path(root)
    if root_path.is_file():
        root_path = root_path.parent

    defaults = FlightCheckConfig().model_dump()
    merged = defaults

    config_path = root_path / CONFIG_FILENAME
    if config_path.is_file():
        file_data = _read_toml(config_path)
        _reject_secrets(file_data, CONFIG_FILENAME)
        merged = _deep_merge(merged, _normalise_file_data(file_data))

    merged = _deep_merge(merged, _env_data(env))

    if overrides:
        cleaned = _drop_none(overrides)
        _reject_secrets(cleaned, "Command-line options")
        merged = _deep_merge(merged, cleaned)

    try:
        config = FlightCheckConfig.model_validate(merged)
    except ValidationError as exc:
        raise ConfigError(
            f"Invalid configuration: {_format_validation_error(exc)}",
            hint=f"Check {CONFIG_FILENAME} and FLIGHTCHECK_* environment variables.",
        ) from exc

    for provider in config.providers.values():
        value = env.get(provider.api_key_env)
        if value:
            register_secret(value)
            provider.api_key = SecretStr(value)
        else:
            provider.api_key = None
    return config
