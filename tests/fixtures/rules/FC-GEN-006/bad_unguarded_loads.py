"""FC-GEN-006 bad: tool arguments parsed with no error handling."""

# INTENTIONALLY FLAWED FIXTURE: malformed arguments crash the loop.
import json


def run_tool(call):
    args = json.loads(call.arguments)
    return f"weather in {args['city']}: sunny"
