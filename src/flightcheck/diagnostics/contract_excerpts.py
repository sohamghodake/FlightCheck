"""BharatCode contract excerpts sent as context by ``flightcheck explain`` (plan §19.3, ADR-031).

A Python dict copied verbatim from ``docs/BHARATCODE_CONTRACT.md`` §15.1 (the package does not
ship the Markdown file). ``tests/diagnostics/test_context.py`` checks that every
``(topic, text)`` pair still appears on one line of that document, so the copy cannot drift
silently. Rows of the "Not documented" table keep their **[UNVERIFIED]** label; rows of the
verified table are prefixed ``[VERIFIED]``. Never add a fact that is not in the document.
"""

from __future__ import annotations

from collections.abc import Iterable

# Verified rows of contract §15.1 ("Everything in this table is [VERIFIED] unless stated").
VERIFIED: dict[str, str] = {
    "Authentication": (
        "`Authorization: Bearer <BHARATCODE_API_KEY>`; developer key obtained at "
        "<https://bharatcode.ai/account>; keep it server-side, never in browser bundles or "
        "mobile apps"
    ),
    "Models": (
        "`qwen-3.8-27b` (coding, reasoning, images, tool use; `/responses` and "
        "`/chat/completions`; Students 10M tokens/day, Lite, Pro). `deepseek-v4.1-flash` "
        "(coding, long-context reasoning, images, tool use; both endpoints; **Pro only**)"
    ),
    "Access": (
        "API access requires Lite, Pro, or verified student access. Lite and Pro have no "
        "daily/weekly token allowance. All requests share capacity, may queue, and remain "
        "subject to context, output and queue limits"
    ),
    "`model` field": "Required on every request",
    "Responses — input": (
        "Text or message array, optional `instructions`; use `store:false`; read text and "
        "function calls from the `output` array"
    ),
    "Responses — statelessness": "Stateless: replay history and function-call outputs in `input`",
    "Responses — unsupported (→ `400`)": (
        "`store:true`, `previous_response_id`, hosted tools, background jobs, audio/video/file "
        "input"
    ),
    "Responses — streaming": (
        "`stream:true` → SSE. Text in `response.output_text.delta`; finish on "
        "`response.completed` or `response.incomplete`; `response.failed` is an error; queue "
        "keepalive comments are not model output"
    ),
    "Truncation (Responses)": (
        '`status:"incomplete"` with `incomplete_details.reason:"max_output_tokens"` means '
        "output was cut short even with HTTP 200; the budget includes reasoning"
    ),
    "Truncation (Chat)": (
        '`finish_reason:"length"` means the output budget was exhausted even with HTTP 200'
    ),
    "Handling truncation": (
        "Preserve partial output; do not execute incomplete tool calls; raise "
        "`max_output_tokens` / `max_tokens` (up to 32 000) or use "
        '`reasoning:{effort:"none"}` / `reasoning_effort:"none"` for short answers; do not '
        "automatically retry the same exhausted budget"
    ),
    "Function tools (Responses)": (
        "Define with `name` and `parameters` (example: "
        '`{"type":"function","name":...,"description":...,"parameters":{...,'
        '"additionalProperties":false},"strict":true}`, '
        '`tool_choice:{"type":"function","name":...}`). The application validates arguments '
        "and executes; BharatCode does not run tools. Next request: original input + the "
        "`response.output` items + a `function_call_output` item with the matching `call_id` "
        "and a **string** `output`. Keep `store:false`; resend tool definitions still needed"
    ),
    "Busy": (
        "`503` = model busy: wait a few seconds and retry. A stream that has already started "
        "ends with `response.failed` instead"
    ),
}

# Rows of the "Not documented — must be verified" table; the text keeps its label.
UNVERIFIED: dict[str, str] = {
    "Behaviour when `store` is omitted on `/responses`": "**[UNVERIFIED]**",
    "Queue wait limits / client timeout guidance": (
        "**[UNVERIFIED]**; BharatCode's desktop client used a 5-minute response-header "
        "timeout while queued **[OBSERVED]**"
    ),
}

_UNSUPPORTED = "Responses — unsupported (→ `400`)"
_TOOLS = "Function tools (Responses)"
_STATELESS = "Responses — statelessness"
_INPUT = "Responses — input"
_TRUNC = ("Truncation (Responses)", "Truncation (Chat)", "Handling truncation")

# Rule / check ID → contract topics (mapping of contract §15.2).
RULE_TOPICS: dict[str, tuple[str, ...]] = {
    "FC-BC-001": (_UNSUPPORTED, _INPUT),
    "FC-BC-002": (_UNSUPPORTED, _STATELESS),
    "FC-BC-003": (_UNSUPPORTED, _TOOLS),
    "FC-BC-004": (_UNSUPPORTED,),
    "FC-BC-005": ("Handling truncation",),
    "FC-BC-006": ("Models", "`model` field"),
    "FC-BC-007": (_INPUT, "Behaviour when `store` is omitted on `/responses`"),
    "FC-BC-008": (_UNSUPPORTED,),
    "FC-GEN-001": _TRUNC,
    "FC-GEN-002": _TRUNC,
    "FC-GEN-003": ("Responses — streaming",),
    "FC-GEN-004": ("Busy",),
    "FC-GEN-005": ("Access", "Queue wait limits / client timeout guidance"),
    "FC-GEN-006": (_TOOLS,),
    "FC-GEN-007": (_TOOLS,),
    "FC-SEC-001": ("Authentication",),
    "FC-SEC-002": ("Authentication",),
    "FC-RT-001": (_UNSUPPORTED, _INPUT),
    "FC-RT-002": (_UNSUPPORTED, _STATELESS),
    "FC-RT-003": (_UNSUPPORTED, _TOOLS),
    "FC-RT-004": (_UNSUPPORTED,),
    "FC-RT-005": (_UNSUPPORTED,),
    "FC-RT-006": ("Handling truncation",),
    "FC-RT-007": ("Models", "`model` field"),
    "FC-RT-011": (_TOOLS,),
    "FC-RT-012": (_TOOLS,),
    "FC-RT-013": (_TOOLS,),
    "FC-RT-014": (_TOOLS, _STATELESS),
    "FC-RT-020": _TRUNC,
    "FC-RT-022": ("Handling truncation",),
    "FC-RT-023": ("Handling truncation",),
    "FC-RT-030": ("Responses — streaming", "Busy"),
    "FC-RT-040": ("Busy",),
    "FC-RT-041": ("Busy",),
    "FC-RT-050": ("Authentication",),
}


def topic_line(topic: str) -> str:
    """One excerpt line, labelled with its verification tag."""
    if topic in VERIFIED:
        return f"[VERIFIED] {topic}: {VERIFIED[topic]}"
    return f"{topic}: {UNVERIFIED[topic]}"


def excerpt_for(rule_id: str) -> str | None:
    """The contract excerpt for ``rule_id``, or ``None`` when no documented row applies."""
    topics = RULE_TOPICS.get(rule_id)
    if not topics:
        return None
    return "\n".join(topic_line(topic) for topic in topics)


def excerpts_for(rule_ids: Iterable[str]) -> dict[str, str]:
    """``{rule_id: excerpt}`` for every ID that has one, in the given order."""
    out: dict[str, str] = {}
    for rule_id in rule_ids:
        text = excerpt_for(rule_id)
        if text is not None:
            out[rule_id] = text
    return out
