"""Small, side-effect-free helpers over ``ast`` nodes.

Nothing here evaluates code: ``literal_value`` uses ``ast.literal_eval`` on literal node types
only, and everything else is reported as :class:`Unknown`.
"""

from __future__ import annotations

import ast
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
