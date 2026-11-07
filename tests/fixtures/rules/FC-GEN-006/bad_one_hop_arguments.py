"""FC-GEN-006 bad: arguments bound to a name, then parsed without error handling."""

# INTENTIONALLY FLAWED FIXTURE: malformed arguments crash the loop.
import json


def run_tool(tool_call):
    raw = tool_call.function.arguments
    args = json.loads(raw)
    return f"weather in {args['city']}: sunny"
