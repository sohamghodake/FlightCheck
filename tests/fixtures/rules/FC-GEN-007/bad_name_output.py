"""FC-GEN-007 bad (low confidence): output is a name that may hold a dict."""


# INTENTIONALLY FLAWED FIXTURE: the tool output may not be a string.
def tool_result(call, result):
    return {"type": "function_call_output", "call_id": call.call_id, "output": result}
