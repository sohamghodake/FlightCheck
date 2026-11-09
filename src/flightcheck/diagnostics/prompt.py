"""Prompt and request construction for ``explain`` (plan §19.5).

The request goes through providers' ``ResponsesRequest`` (fields ``text_format`` and
``reasoning_effort``); the adapter serialises them as ``text.format`` and ``reasoning``.
``preview_payload`` mirrors ``BharatCodeAdapter.responses_payload`` for ``--dry-run``, which
must not need an API key; a test keeps the two identical.
"""

from __future__ import annotations

import json
from typing import Any

from flightcheck.diagnostics.context import ContextLevel, Group
from flightcheck.diagnostics.contract_excerpts import excerpts_for
from flightcheck.diagnostics.sanitize import Sanitizer
from flightcheck.diagnostics.schema import text_format
from flightcheck.providers.base import ResponsesRequest

INSTRUCTIONS = """\
You are FlightCheck's diagnostic explainer. A deterministic analysis tool has already produced \
the findings below. You must NOT decide whether a finding is valid and must NOT invent API \
behaviour. Use only the BharatCode contract excerpts provided; excerpts labelled [UNVERIFIED] \
are not confirmed behaviour and must be described as unconfirmed. For each finding group: \
explain the root cause, the practical impact, and the smallest fix. If the evidence is \
insufficient, say so in "caveats". Only propose a code patch when a snippet is provided, and \
only by quoting the snippet's original lines exactly. Respond with JSON matching the schema."""

# Without text.format (prompt-only mode) the schema has to be spelled out in the prompt.
PROMPT_ONLY_SUFFIX = """

Respond with one JSON object and nothing else (no Markdown, no code fences), shaped exactly \
like this; use only rule_id and finding_id values from the input:
{"explanations": [{"rule_id": "...", "finding_ids": ["..."], "summary": "≤600 chars", \
"root_cause": "≤1200 chars", "impact": "≤800 chars", "fix_steps": ["≤6 items"], \
"patch": null, "caveats": ["≤5 items"], "model_stated_confidence": "low|medium|high"}]}
"patch" may instead be {"path": "...", "original_snippet": "...", "proposed_snippet": "..."} \
only when a snippet was provided for that path."""

PROVIDER = "bharatcode"

__all__ = [
    "INSTRUCTIONS",
    "PROMPT_ONLY_SUFFIX",
    "build_input",
    "build_request",
    "preview_payload",
]


def build_input(groups: list[Group], level: ContextLevel, sanitizer: Sanitizer) -> str:
    """The ``input`` text: one sanitised JSON document (§19.5 "[input]")."""
    document = {
        "provider": PROVIDER,
        "context_level": level,
        "contract_excerpts": excerpts_for(group.rule_id for group in groups),
        "groups": [group.to_prompt() for group in groups],
    }
    sanitised = sanitizer.obj(document)
    # Snippets may exceed the generic string cap (§19.3 limits apply instead, ADR-031): put
    # them back after sanitising everything else, already redacted by context.select.
    for out_group, group in zip(sanitised["groups"], groups, strict=True):
        for out_instance, instance in zip(out_group["instances"], group.instances, strict=True):
            if instance.snippet is not None:
                out_instance["snippet"] = instance.snippet
    return json.dumps(sanitised, ensure_ascii=False, indent=1)


def build_request(
    input_text: str,
    *,
    model: str,
    max_output_tokens: int,
    reasoning_none: bool,
    structured: bool = True,
) -> ResponsesRequest:
    """``POST /responses`` with ``store=false``; ``text.format`` only when ``structured``."""
    return ResponsesRequest(
        model=model,
        input=input_text,
        instructions=INSTRUCTIONS if structured else INSTRUCTIONS + PROMPT_ONLY_SUFFIX,
        text_format=text_format() if structured else None,
        max_output_tokens=max_output_tokens,
        reasoning_effort="none" if reasoning_none else None,
        store=False,
    )


def preview_payload(req: ResponsesRequest) -> dict[str, Any]:
    """The JSON body the BharatCode adapter would send for ``req`` (no key needed)."""
    payload: dict[str, Any] = {"model": req.model, "input": req.input}
    if req.instructions is not None:
        payload["instructions"] = req.instructions
    if req.text_format is not None:
        payload["text"] = {"format": req.text_format}
    if req.max_output_tokens is not None:
        payload["max_output_tokens"] = req.max_output_tokens
    if req.reasoning_effort is not None:
        payload["reasoning"] = {"effort": req.reasoning_effort}
    payload["store"] = req.store
    return payload
