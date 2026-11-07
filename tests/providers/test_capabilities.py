"""Capability profiles, the tightened core types, and the providers import boundary."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from flightcheck.core import capabilities as core_capabilities
from flightcheck.core.config import (
    BHARATCODE_DOCUMENTED_MODELS,
    MAX_OUTPUT_TOKENS_CAP,
    ProviderConfig,
    load_config,
)
from flightcheck.core.errors import ConfigError
from flightcheck.core.models import Report
from flightcheck.providers import capabilities
from flightcheck.providers.capabilities import (
    BHARATCODE_PROFILE,
    BHARATCODE_SOURCE,
    Capability,
    CapabilityEntry,
    CapabilityProfile,
    SupportLevel,
    bharatcode_profile,
    generic_profile,
)

ROOT = Path(__file__).resolve().parents[2]
PROVIDERS_DIR = ROOT / "src" / "flightcheck" / "providers"
CONTRACT = ROOT / "docs" / "BHARATCODE_CONTRACT.md"

S, U, K = (
    SupportLevel.SUPPORTED_DOCUMENTED,
    SupportLevel.UNSUPPORTED_DOCUMENTED,
    SupportLevel.UNKNOWN,
)

# Hand-checked against BHARATCODE_CONTRACT.md §15.1 (supported / unsupported → 400 / not
# documented).
EXPECTED_LEVELS = {
    Capability.LIST_MODELS: S,
    Capability.RESPONSES_API: S,
    Capability.CHAT_COMPLETIONS: S,
    Capability.STREAM_RESPONSES: S,
    Capability.STREAM_CHAT: K,
    Capability.FUNCTION_TOOLS: S,
    Capability.HOSTED_TOOLS: U,
    Capability.STRUCTURED_OUTPUT_TEXT_FORMAT: S,
    Capability.STRUCTURED_OUTPUT_RESPONSE_FORMAT: K,
    Capability.STORE_TRUE: U,
    Capability.PREVIOUS_RESPONSE_ID: U,
    Capability.BACKGROUND: U,
    Capability.IMAGE_INPUT: S,
    Capability.AUDIO_INPUT: U,
    Capability.VIDEO_INPUT: U,
    Capability.FILE_INPUT: U,
    Capability.REASONING_EFFORT_NONE: S,
}


def test_bharatcode_profile_matches_the_contract() -> None:
    assert set(EXPECTED_LEVELS) == set(Capability)
    for cap, level in EXPECTED_LEVELS.items():
        assert BHARATCODE_PROFILE.level(cap) is level, cap


def test_every_bharatcode_entry_cites_the_documentation_url() -> None:
    assert BHARATCODE_SOURCE in CONTRACT.read_text(encoding="utf-8")
    for cap in Capability:
        entry = BHARATCODE_PROFILE.entry(cap)
        assert entry.source == "https://bharatcode.ai/build", cap
        assert entry.note, cap


def test_bharatcode_profile_limits_and_models() -> None:
    assert BHARATCODE_PROFILE.provider == "bharatcode"
    assert BHARATCODE_PROFILE.base_url == "https://bharatcode.ai/api/model/v1"
    assert BHARATCODE_PROFILE.max_output_tokens == MAX_OUTPUT_TOKENS_CAP == 32_000
    assert set(BHARATCODE_PROFILE.documented_models) == BHARATCODE_DOCUMENTED_MODELS


def test_missing_entry_defaults_to_unknown() -> None:
    profile = CapabilityProfile(provider="x", base_url="https://x", entries={})
    assert profile.level(Capability.BACKGROUND) is SupportLevel.UNKNOWN
    assert profile.entry(Capability.BACKGROUND).source == "default"


def test_profiles_and_entries_are_frozen() -> None:
    with pytest.raises(ValueError):
        BHARATCODE_PROFILE.base_url = "https://elsewhere"  # type: ignore[misc]
    with pytest.raises(ValueError):
        BHARATCODE_PROFILE.entry(Capability.STORE_TRUE).level = S  # type: ignore[misc]


def test_bharatcode_profile_copy_does_not_touch_the_constant() -> None:
    profile = bharatcode_profile("http://127.0.0.1:9/api/model/v1/")
    assert profile.base_url == "http://127.0.0.1:9/api/model/v1"
    profile.entries[Capability.STREAM_CHAT] = CapabilityEntry(level=S, source="test")
    assert BHARATCODE_PROFILE.level(Capability.STREAM_CHAT) is K


def test_declarations_fill_only_unknown_bharatcode_entries() -> None:
    profile = bharatcode_profile(
        declared={Capability.STREAM_CHAT: True, Capability.STORE_TRUE: True}
    )
    assert profile.level(Capability.STREAM_CHAT) is SupportLevel.DECLARED
    assert profile.entry(Capability.STREAM_CHAT).source == "flightcheck.toml"
    # A documented fact cannot be declared away.
    assert profile.level(Capability.STORE_TRUE) is U


def test_generic_profile_is_unknown_except_declarations() -> None:
    profile = generic_profile(
        {Capability.FUNCTION_TOOLS: True, Capability.BACKGROUND: False},
        base_url="https://llm.example.com/v1/",
    )
    assert profile.provider == "openai_compatible"
    assert profile.base_url == "https://llm.example.com/v1"
    assert profile.level(Capability.FUNCTION_TOOLS) is SupportLevel.DECLARED
    assert profile.level(Capability.BACKGROUND) is SupportLevel.DECLARED_UNSUPPORTED
    others = set(Capability) - {Capability.FUNCTION_TOOLS, Capability.BACKGROUND}
    assert all(profile.level(cap) is K for cap in others)
    assert generic_profile().entries.keys() == set(Capability)


def test_providers_reexport_the_core_types() -> None:
    assert capabilities.Capability is core_capabilities.Capability
    assert capabilities.CapabilityProfile is core_capabilities.CapabilityProfile
    assert capabilities.SupportLevel is core_capabilities.SupportLevel


# --- Tightened placeholders (ADR-023 item 7) ------------------------------------------------


def test_report_capability_profile_is_typed() -> None:
    field = Report.model_fields["capability_profile"]
    assert "CapabilityProfile" in str(field.annotation)
    schema = Report.model_json_schema()
    assert "CapabilityProfile" in schema["$defs"]
    levels = schema["$defs"]["SupportLevel"]["enum"]
    assert "declared_unsupported" in levels and "supported_documented" in levels


def test_capability_profile_round_trips_through_json() -> None:
    data = json.loads(BHARATCODE_PROFILE.model_dump_json())
    assert data["entries"]["store_true"]["level"] == "unsupported_documented"
    assert CapabilityProfile.model_validate(data) == BHARATCODE_PROFILE


def test_declared_capabilities_are_capability_keys() -> None:
    config = ProviderConfig(
        name="x",
        kind="openai_compatible",
        base_url="https://x",
        declared_capabilities={"function_tools": True},  # type: ignore[dict-item]
    )
    assert config.declared_capabilities == {Capability.FUNCTION_TOOLS: True}
    assert isinstance(next(iter(config.declared_capabilities)), Capability)
    with pytest.raises(ValueError):
        ProviderConfig(
            name="x",
            kind="openai_compatible",
            base_url="https://x",
            declared_capabilities={"teleport": True},  # type: ignore[dict-item]
        )


def test_declared_capabilities_from_toml(tmp_path: Path) -> None:
    (tmp_path / "flightcheck.toml").write_text(
        "[providers.local]\nkind = 'openai_compatible'\nbase_url = 'https://llm.example.com/v1'\n"
        "[providers.local.declared_capabilities]\nstream_chat = true\nbackground = false\n",
        encoding="utf-8",
    )
    config = load_config(tmp_path, environ={})
    assert config.providers["local"].declared_capabilities == {
        Capability.STREAM_CHAT: True,
        Capability.BACKGROUND: False,
    }


def test_unknown_declared_capability_in_toml_is_a_config_error(tmp_path: Path) -> None:
    (tmp_path / "flightcheck.toml").write_text(
        "[providers.bharatcode.declared_capabilities]\nteleport = true\n", encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="declared_capabilities"):
        load_config(tmp_path, environ={})


# --- Import boundary (ADR-003, docs/ARCHITECTURE.md §3) -------------------------------------

FORBIDDEN_IMPORTS = {
    "openai",
    "flightcheck.mock",
    "flightcheck.scanner",
    "flightcheck.runtime",
    "flightcheck.cli",
    "flightcheck.orchestrator",
}


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_providers_import_no_sdk_mock_scanner_or_runtime() -> None:
    files = sorted(PROVIDERS_DIR.glob("*.py"))
    assert {p.name for p in files} >= {
        "__init__.py",
        "capabilities.py",
        "base.py",
        "http.py",
        "sse.py",
        "bharatcode.py",
        "registry.py",
    }
    offenders = [
        (path.name, name)
        for path in files
        for name in _imports(path)
        if any(name == bad or name.startswith(bad + ".") for bad in FORBIDDEN_IMPORTS)
    ]
    assert offenders == []


def test_flightcheck_imports_in_providers_are_core_or_providers() -> None:
    for path in sorted(PROVIDERS_DIR.glob("*.py")):
        for name in _imports(path):
            if name.startswith("flightcheck.") and not name.startswith("flightcheck.__"):
                assert name.startswith(("flightcheck.core", "flightcheck.providers")), (
                    path.name,
                    name,
                )
