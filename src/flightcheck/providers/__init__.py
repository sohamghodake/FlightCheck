"""Provider adapters (plan §14): raw HTTP over httpx2, synchronous, capability-aware.

Imports only ``flightcheck.core`` (docs/ARCHITECTURE.md §3): never the OpenAI SDK, the mock,
the scanner or the runtime (ADR-003).
"""

from flightcheck.providers.base import (
    ChatRequest,
    FinishState,
    ModelInfo,
    NormalizedResponse,
    ProviderAdapter,
    RawResponse,
    ResponsesRequest,
    StreamEvent,
    ToolCall,
    Usage,
)
from flightcheck.providers.bharatcode import BharatCodeAdapter
from flightcheck.providers.capabilities import (
    BHARATCODE_PROFILE,
    Capability,
    CapabilityEntry,
    CapabilityProfile,
    SupportLevel,
    bharatcode_profile,
    generic_profile,
)
from flightcheck.providers.registry import build_adapter

__all__ = [
    "BHARATCODE_PROFILE",
    "BharatCodeAdapter",
    "Capability",
    "CapabilityEntry",
    "CapabilityProfile",
    "ChatRequest",
    "FinishState",
    "ModelInfo",
    "NormalizedResponse",
    "ProviderAdapter",
    "RawResponse",
    "ResponsesRequest",
    "StreamEvent",
    "SupportLevel",
    "ToolCall",
    "Usage",
    "bharatcode_profile",
    "build_adapter",
    "generic_profile",
]
