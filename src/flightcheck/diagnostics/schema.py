"""Output schema for AI explanations (plan §19.6) and its JSON Schema for ``text.format``.

``AIPatch`` and ``AIExplanation`` live in ``core.models`` (core may not import diagnostics and
``Report.ai_suggestions`` needs them); they are re-exported here (ADR-031). The batch wrapper
is only ever parsed by the diagnostic service, so it lives here.

The ``text.format`` sub-schema (``type: "json_schema"``, ``name``, ``schema``, ``strict``) is
**[UNVERIFIED]** for BharatCode (contract §15.1, probe P-07): the service retries once without
it on a 400 and relies on prompt instructions plus strict Pydantic validation.
"""

from __future__ import annotations

import copy
from typing import Any

from pydantic import BaseModel, ConfigDict

from flightcheck.core.models import AIExplanation, AIPatch, AISuggestion

SCHEMA_NAME = "flightcheck_explanations"

__all__ = [
    "SCHEMA_NAME",
    "AIExplanation",
    "AIExplanationBatch",
    "AIPatch",
    "AISuggestion",
    "explanation_json_schema",
    "text_format",
]


class AIExplanationBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    explanations: list[AIExplanation]


def _inline(node: Any, defs: dict[str, Any]) -> Any:
    """Resolve ``$ref``s, drop ``title``/``default``, and require every property (OpenAI-style
    strict schemas list all properties as required; optional ones are nullable)."""
    if isinstance(node, list):
        return [_inline(item, defs) for item in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        name = node["$ref"].rsplit("/", 1)[-1]
        return _inline(copy.deepcopy(defs[name]), defs)
    out = {
        key: _inline(value, defs)
        for key, value in node.items()
        if key not in ("title", "default", "$defs")
    }
    if out.get("type") == "object" and "properties" in out:
        out["required"] = list(out["properties"])
        out["additionalProperties"] = False
    return out


def explanation_json_schema() -> dict[str, Any]:
    """The JSON Schema of :class:`AIExplanationBatch`, self-contained (no ``$ref``)."""
    raw = AIExplanationBatch.model_json_schema()
    return _inline(raw, raw.get("$defs", {}))


def text_format() -> dict[str, Any]:
    """The ``text.format`` object sent on ``/responses`` (sub-schema [UNVERIFIED], P-07)."""
    return {
        "type": "json_schema",
        "name": SCHEMA_NAME,
        "schema": explanation_json_schema(),
        "strict": True,
    }
