"""Small, side-effect-free helpers over ``ast`` nodes.

Nothing here evaluates code: ``literal_value`` uses ``ast.literal_eval`` on literal node types
only, and everything else is reported as :class:`Unknown`.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

SEGMENT_MAX_LEN = 300

_LITERAL_NODES = (ast.Constant, ast.List, ast.Tuple, ast.Dict, ast.Set, ast.UnaryOp)


@dataclass(frozen=True)
class Unknown:
    """A value that is not a plain literal; ``source`` is its (truncated) source text."""

    source: str

    def __str__(self) -> str:
        return f"Unknown({self.source})"


def attr_chain(node: ast.AST) -> list[str] | None:
    """``client.chat.completions.create`` → ``["client", "chat", "completions", "create"]``.

    Returns ``None`` unless the chain is made only of ``Attribute`` nodes ending in a ``Name``.
    """
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    parts.reverse()
    return parts


def split_attrs(node: ast.AST) -> tuple[ast.AST, list[str]]:
    """Split ``<base>.a.b.c`` into ``(base, ["a", "b", "c"])``; base is the first non-attribute."""
    attrs: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        attrs.append(current.attr)
        current = current.value
    attrs.reverse()
    return current, attrs


def source_segment(source: str, node: ast.AST, max_len: int = SEGMENT_MAX_LEN) -> str:
    """Source text of ``node`` (falls back to ``ast.unparse``), cut to ``max_len`` with ``…``."""
    text: str | None = None
    try:
        text = ast.get_source_segment(source, node)
    except (ValueError, IndexError, TypeError):
        text = None
    if not text:
        try:
            text = ast.unparse(node)
        except (ValueError, TypeError, AttributeError, RecursionError):
            text = f"<{type(node).__name__}>"
    if len(text) > max_len:
        text = text[: max_len - 1] + "…"
    return text


def literal_value(node: ast.AST, source: str = "") -> Any:
    """Return the Python value of a literal node, or :class:`Unknown` for anything else.

    Only ``Constant``, ``List``, ``Tuple``, ``Dict``, ``Set`` and ``UnaryOp`` nodes are passed to
    ``ast.literal_eval`` (which itself rejects names, calls and operators inside them).
    """
    if isinstance(node, _LITERAL_NODES):
        try:
            return ast.literal_eval(node)
        except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
            pass
    return Unknown(source_segment(source, node))


def is_unknown(value: Any) -> bool:
    return isinstance(value, Unknown)


NameResolver = Callable[[str], str | None]


def string_fragments(node: ast.AST, resolve_name: NameResolver | None = None) -> list[str]:
    """Constant string pieces of a URL-like expression, in source order.

    Handles string constants, f-strings (constant parts only), ``+`` concatenation, ``%``
    formatting and ``"...".format(...)`` (the template). ``resolve_name`` may map a bare name to a
    string constant it was bound to (one hop); other dynamic parts contribute nothing.
    """
    if isinstance(node, ast.Constant):
        return [node.value] if isinstance(node.value, str) else []
    if isinstance(node, ast.JoinedStr):
        out: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                out.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                out.extend(string_fragments(value.value, resolve_name))
        return out
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return string_fragments(node.left, resolve_name) + string_fragments(
            node.right, resolve_name
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        return string_fragments(node.left, resolve_name)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "format"
    ):
        return string_fragments(node.func.value, resolve_name)
    if isinstance(node, ast.Name) and resolve_name is not None:
        resolved = resolve_name(node.id)
        return [resolved] if resolved is not None else []
    return []


# --- Name and secret classification (Phase 4: FC-SEC-001/002 and excerpt masking) ----------

_WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")
_CREDENTIAL_WORDS = frozenset({"apikey", "secret", "token", "authorization"})

# Prefixed key patterns (FC-SEC-001). ``bc_live_`` is the BharatCode prefix [OBSERVED].
SECRET_PREFIX_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?<![A-Za-z0-9])bc_live_[A-Za-z0-9_\-]{8,}"), "bc_live_…[REDACTED]"),
    (re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_\-]{16,}"), "sk-…[REDACTED]"),
)
# Placeholder values (compared lower-cased) are reported as info. The prefixed one is split so
# FlightCheck's own source holds no literal its FC-SEC-001 would match (dogfood gate, §26.2).
PLACEHOLDER_KEYS = frozenset(
    {"", "empty", "dummy", "test", "your_key_here", "bc_live_" + "your_key_here"}
)


def name_words(name: str) -> list[str]:
    """``OPENAI_API_KEY`` → ``["openai", "api", "key"]``; ``apiKey`` → ``["api", "key"]``."""
    return [word.lower() for word in _WORD.findall(name)]


def is_api_key_name(name: str) -> bool:
    """``api_key``, ``apiKey``, ``OPENAI_API_KEY``, ``x-api-key``: words end in api + key."""
    words = name_words(name)
    return words[-2:] == ["api", "key"] or (bool(words) and words[-1] == "apikey")


def is_credential_name(name: str) -> bool:
    """A name that looks like a credential (FC-SEC-002): api key, secret, token, authorization.

    Matching is by whole word, so ``total_tokens``/``max_tokens`` are not credentials.
    """
    words = name_words(name)
    if any(a == "api" and b == "key" for a, b in zip(words, words[1:], strict=False)):
        return True
    return any(word in _CREDENTIAL_WORDS for word in words)


def is_placeholder_key(value: str) -> bool:
    return value.strip().lower() in PLACEHOLDER_KEYS


def prefixed_secrets(value: str) -> list[tuple[str, str]]:
    """``(matched key, masked replacement)`` for every prefixed key inside ``value``."""
    found: list[tuple[str, str]] = []
    for pattern, replacement in SECRET_PREFIX_PATTERNS:
        found.extend((match.group(0), replacement) for match in pattern.finditer(value))
    return found
