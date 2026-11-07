"""FC-GEN-006 good: arguments parsed inside try/except json.JSONDecodeError."""

import json


def run_tool(call):
    try:
        args = json.loads(call.arguments)
    except json.JSONDecodeError as exc:
        return f"error: invalid arguments ({exc})"
    return f"weather in {args.get('city')}: sunny"
