"""FC-GEN-007 good: tool outputs are json.dumps(...) strings and f-strings."""

import json


def tool_result(call, result):
    return {"type": "function_call_output", "call_id": call.call_id, "output": json.dumps(result)}


def tool_message(tool_call, temp):
    return {"role": "tool", "tool_call_id": tool_call.id, "content": f"{temp} C"}
