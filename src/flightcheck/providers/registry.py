"""Adapter factory: provider config → adapter (plan §27 Phase 6 task 6)."""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx2

from flightcheck.core.config import ProviderConfig
from flightcheck.core.errors import ConfigError
from flightcheck.providers.base import ProviderAdapter
from flightcheck.providers.bharatcode import BharatCodeAdapter

__all__ = ["build_adapter"]


def build_adapter(
    provider_config: ProviderConfig,
    *,
    transport: httpx2.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> ProviderAdapter:
    """The adapter for ``provider_config.kind``.

    ``bharatcode`` → :class:`BharatCodeAdapter`. The generic ``openai_compatible`` adapter is
    P1 and not built yet, so it raises :class:`ConfigError`.
    """
    if provider_config.kind == "bharatcode":
        return BharatCodeAdapter(provider_config, transport=transport, sleep=sleep)
    raise ConfigError(
        f"Provider '{provider_config.name}' has kind '{provider_config.kind}', which has no "
        "adapter yet (the generic OpenAI-compatible adapter is planned, P1).",
        hint="Use the built-in 'bharatcode' provider.",
    )
