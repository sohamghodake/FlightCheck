"""BharatCode request-contract checks shared by the mock and the provider adapter (pure).

The mock answers these with HTTP 400 (ADR-014); the BharatCode adapter refuses them locally
before any network call (plan §14.7). Both use this one implementation so they cannot drift
apart (ADR-028). The facts come from BHARATCODE_CONTRACT.md §15.1, "Responses — unsupported
(→ 400)"; the error codes are [UNVERIFIED].
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flightcheck.core.config import FILE_INPUT_KEYS, UNSUPPORTED_INPUT_TYPES

_INPUT_DEPTH_LIMIT = 20


@dataclass(frozen=True)
class Violation:
    field: str
    code: str  # [UNVERIFIED]: BharatCode's error codes are not documented
    message: str


def _unsupported_input(value: Any, depth: int = 0) -> list[str]:
    """Same walk as FC-BC-008: unsupported item types and file keys anywhere in ``input``."""
    if depth > _INPUT_DEPTH_LIMIT:
        return []
    found: list[str] = []
    if isinstance(value, dict):
        item_type = value.get("type")
        if isinstance(item_type, str) and item_type in UNSUPPORTED_INPUT_TYPES:
            found.append(item_type)
        found.extend(key for key in FILE_INPUT_KEYS if key in value)
        for child in value.values():
            found.extend(_unsupported_input(child, depth + 1))
    elif isinstance(value, list):
        for child in value:
            found.extend(_unsupported_input(child, depth + 1))
    return found


def _hosted_tool_types(tools: Any) -> list[str]:
    if not isinstance(tools, list):
        return []
    found = {
        tool["type"]
        for tool in tools
        if isinstance(tool, dict) and isinstance(tool.get("type"), str)
        if tool["type"] != "function"
    }
    return sorted(found)


def responses_contract_violations(request_json: dict[str, Any]) -> list[Violation]:
    """The /responses features BharatCode documents as unsupported (BHARATCODE_CONTRACT.md
    §15.1, "Responses — unsupported (→ 400)"), in a fixed order. Mirrors FC-BC-001…004/008."""
    found: list[Violation] = []
    if request_json.get("store") is True:
        found.append(
            Violation(
                "store",
                "unsupported_parameter",
                "store=true is not supported: send store=false and replay history in input.",
            )
        )
    # A JSON null is treated as "not used"; whether BharatCode rejects the key itself is
    # [UNVERIFIED].
    if request_json.get("previous_response_id") is not None:
        found.append(
            Violation(
                "previous_response_id",
                "unsupported_parameter",
                "previous_response_id is not supported: the Responses API is stateless; "
                "replay prior turns and output items in input.",
            )
        )
    hosted = _hosted_tool_types(request_json.get("tools"))
    if hosted:
        found.append(
            Violation(
                "tools",
                "unsupported_tool_type",
                f"Hosted tools are not supported ({', '.join(t[:80] for t in hosted)}): "
                "define your own function tools.",
            )
        )
    if request_json.get("background") is True:
        found.append(
            Violation(
                "background",
                "unsupported_parameter",
                "background=true is not supported: run the request synchronously.",
            )
        )
    unsupported = sorted(set(_unsupported_input(request_json.get("input"))))
    if unsupported:
        found.append(
            Violation(
                "input",
                "unsupported_input_type",
                f"Unsupported input ({', '.join(unsupported)}): audio, video and file input are "
                "not supported; send text or images.",
            )
        )
    return found
