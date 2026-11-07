"""Provider capability model (plan §14.2). Pure data.

These types live in core because ``Report.capability_profile`` and
``ProviderConfig.declared_capabilities`` reference them and core may import only the standard
library and pydantic. ``flightcheck.providers.capabilities`` re-exports them and holds the
BharatCode profile.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class Capability(StrEnum):
    LIST_MODELS = "list_models"
    RESPONSES_API = "responses_api"
    CHAT_COMPLETIONS = "chat_completions"
    STREAM_RESPONSES = "stream_responses"
    STREAM_CHAT = "stream_chat"
    FUNCTION_TOOLS = "function_tools"
    HOSTED_TOOLS = "hosted_tools"
    STRUCTURED_OUTPUT_TEXT_FORMAT = "structured_output_text_format"  # Responses text.format
    STRUCTURED_OUTPUT_RESPONSE_FORMAT = "structured_output_response_format"  # Chat response_format
    STORE_TRUE = "store_true"
    PREVIOUS_RESPONSE_ID = "previous_response_id"
    BACKGROUND = "background"
    IMAGE_INPUT = "image_input"
    AUDIO_INPUT = "audio_input"
    VIDEO_INPUT = "video_input"
    FILE_INPUT = "file_input"
    REASONING_EFFORT_NONE = "reasoning_effort_none"


class SupportLevel(StrEnum):
    SUPPORTED_DOCUMENTED = "supported_documented"  # provider docs say yes
    SUPPORTED_VERIFIED = "supported_verified"  # live probe confirmed
    UNSUPPORTED_DOCUMENTED = "unsupported_documented"  # provider docs say no / 400
    UNSUPPORTED_VERIFIED = "unsupported_verified"  # live probe confirmed rejection
    DECLARED = "declared"  # user asserted support in flightcheck.toml
    DECLARED_UNSUPPORTED = "declared_unsupported"  # user asserted no support (ADR-028)
    UNKNOWN = "unknown"


# Levels under which an operation is refused locally before any network call.
UNSUPPORTED_LEVELS = frozenset(
    {
        SupportLevel.UNSUPPORTED_DOCUMENTED,
        SupportLevel.UNSUPPORTED_VERIFIED,
        SupportLevel.DECLARED_UNSUPPORTED,
    }
)


class CapabilityEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    level: SupportLevel
    source: str  # URL, "flightcheck.toml" or "probe:<id>@<date>"
    note: str = ""


_DEFAULT_ENTRY = CapabilityEntry(level=SupportLevel.UNKNOWN, source="default")


class CapabilityProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str  # "bharatcode" | "openai_compatible"
    base_url: str
    documented_models: tuple[str, ...] = ()
    max_output_tokens: int | None = None
    entries: dict[Capability, CapabilityEntry]

    def entry(self, cap: Capability) -> CapabilityEntry:
        return self.entries.get(cap, _DEFAULT_ENTRY)

    def level(self, cap: Capability) -> SupportLevel:
        return self.entry(cap).level
