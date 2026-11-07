"""FC-GEN-007 bad: a function_call_output item whose output is a dict."""


# INTENTIONALLY FLAWED FIXTURE: the tool output is not a string.
def tool_result(call):
    return {"type": "function_call_output", "call_id": call.call_id, "output": {"temp": 30}}
