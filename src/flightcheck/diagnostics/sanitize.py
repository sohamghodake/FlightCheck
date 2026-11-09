"""Sanitisation of everything ``explain`` sends or prints (plan §19.4).

Applied to every string leaving the process: invalid UTF-8 replaced, absolute paths made
repo-relative (the home directory becomes ``~``), secrets redacted with ``core.redact``,
strings over 2 000 characters truncated with a marker. Recorded headers are dropped entirely.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from flightcheck.core.redact import redact

STRING_LIMIT = 2000
TRUNCATION_MARKER = " …[truncated by FlightCheck]"

# Keys whose values are never sent, whatever they contain (recorded headers, credentials).
DROPPED_KEYS = frozenset(
    {
        "headers",
        "request_headers",
        "response_headers",
        "authorization",
        "api_key",
        "cookie",
        "set-cookie",
    }
)

__all__ = [
    "DROPPED_KEYS",
    "STRING_LIMIT",
    "TRUNCATION_MARKER",
    "Sanitizer",
    "fix_utf8",
    "truncate",
]


def fix_utf8(value: str | bytes) -> str:
    """Decode bytes, and replace lone surrogates in ``str``, with U+FFFD."""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value.encode("utf-8", errors="replace").decode("utf-8")


def truncate(text: str, limit: int = STRING_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER


class Sanitizer:
    """Sanitises strings and nested JSON-like data relative to a repository root."""

    def __init__(self, repo_root: str | os.PathLike[str] | None = None) -> None:
        rules: list[tuple[re.Pattern[str], str]] = []
        roots: list[tuple[str, str]] = []
        if repo_root is not None:
            roots.append((str(Path(repo_root).resolve()), "."))
        home = str(Path.home())
        if home and home != os.sep:
            roots.append((home, "~"))
        for root, alias in roots:
            # "<root>/x" → "x" (repo) or "~/x" (home); "<root>" alone → alias. The lookahead
            # keeps "/home/me2" from matching "/home/me".
            sub = "" if alias == "." else alias + "/"
            rules.append((re.compile(re.escape(root) + r"[/\\]"), sub))
            rules.append((re.compile(re.escape(root) + r"(?![\w.\-])"), alias))
        self._rules = rules

    def paths(self, text: str) -> str:
        for pattern, replacement in self._rules:
            text = pattern.sub(lambda _m, r=replacement: r, text)
        return text

    def text(self, value: str | bytes, *, limit: int | None = STRING_LIMIT) -> str:
        """UTF-8 → paths → redaction → truncation (redaction first, so a secret is never cut
        in half and left partly visible)."""
        out = redact(self.paths(fix_utf8(value)))
        return truncate(out, limit) if limit is not None else out

    def obj(self, value: Any) -> Any:
        """Deep copy with every string sanitised and :data:`DROPPED_KEYS` removed."""
        if isinstance(value, str | bytes):
            return self.text(value)
        if isinstance(value, dict):
            return {
                str(key): self.obj(item)
                for key, item in value.items()
                if str(key).lower() not in DROPPED_KEYS
            }
        if isinstance(value, list | tuple):
            return [self.obj(item) for item in value]
        return value

    def json_text(self, value: Any) -> str:
        """Compact JSON of ``value`` after :meth:`obj`, redacted once more as a whole."""
        text = json.dumps(self.obj(value), ensure_ascii=False, sort_keys=True)
        return redact(self.paths(text))
