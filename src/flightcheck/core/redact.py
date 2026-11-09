"""Secret redaction (defence in depth for every log line, terminal line and report).

Order of application in :func:`redact`:

1. Exact values registered at runtime with :func:`register_secret` → ``[REDACTED]``.
2. Exact values of environment variables whose name looks secret (key, token, secret,
   password) and whose value is at least 8 characters → ``[REDACTED:<ENV_NAME>]``.
3. Pattern rules (BharatCode ``bc_live_`` keys, ``sk-`` keys, bearer tokens,
   ``api_key=...``-style assignments).

Exact values go first so a pattern rule cannot partially rewrite a value before it is matched.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any

REDACTED = "[REDACTED]"

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"bc_live_[A-Za-z0-9_\-]{4,}"), "bc_live_…[REDACTED]"),
    (re.compile(r"sk-[A-Za-z0-9_\-]{12,}"), "sk-…[REDACTED]"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{8,}"), "Bearer [REDACTED]"),
    (
        # The value also stops at a backslash that escapes a quote, so redacting JSON text
        # (where " is written \") never eats the escape and breaks the JSON (Phase 8 fix).
        re.compile(
            r"""(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*["']?"""
            r"""(?:[^\s"'\\]|\\(?!["'])){8,}"""
        ),
        r"\1=[REDACTED]",
    ),
)

_SECRET_ENV_NAME = re.compile(r"(?i)(key|token|secret|password)")
_MIN_ENV_VALUE_LEN = 8

_registered: set[str] = set()


def register_secret(value: str | None) -> None:
    """Redact this exact value everywhere from now on (e.g. a real API key read from env).

    Values shorter than 4 characters are ignored: redacting them would mangle normal text.
    """
    if value and len(value) >= 4:
        _registered.add(value)


def clear_registered_secrets() -> None:
    """Forget every value passed to :func:`register_secret` (used by tests)."""
    _registered.clear()


def _secret_env_values(environ: Mapping[str, str]) -> list[tuple[str, str]]:
    found = [
        (name, value)
        for name, value in environ.items()
        if _SECRET_ENV_NAME.search(name) and len(value) >= _MIN_ENV_VALUE_LEN
    ]
    # Longest first, so a value that contains another value is replaced whole.
    found.sort(key=lambda item: len(item[1]), reverse=True)
    return found


def redact(text: str, *, environ: Mapping[str, str] | None = None) -> str:
    """Return ``text`` with known secrets replaced. Non-secret text is unchanged."""
    if not text:
        return text
    for value in sorted(_registered, key=len, reverse=True):
        if value in text:
            text = text.replace(value, REDACTED)
    for name, value in _secret_env_values(os.environ if environ is None else environ):
        if value in text:
            text = text.replace(value, f"[REDACTED:{name}]")
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact_obj(obj: Any, *, environ: Mapping[str, str] | None = None) -> Any:
    """Deep-redact strings inside dicts, lists and tuples. Returns new containers.

    Dict keys are kept as they are; only values are redacted. Other types are returned
    unchanged.
    """
    if isinstance(obj, str):
        return redact(obj, environ=environ)
    if isinstance(obj, dict):
        return {key: redact_obj(value, environ=environ) for key, value in obj.items()}
    if isinstance(obj, list):
        return [redact_obj(item, environ=environ) for item in obj]
    if isinstance(obj, tuple):
        return tuple(redact_obj(item, environ=environ) for item in obj)
    return obj
