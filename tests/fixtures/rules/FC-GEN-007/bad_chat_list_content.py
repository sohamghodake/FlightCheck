"""FC-GEN-007 bad: a Chat tool message whose content is a list."""


# INTENTIONALLY FLAWED FIXTURE: the tool message content is not a string.
def tool_message(tool_call):
    return {"role": "tool", "tool_call_id": tool_call.id, "content": [30, "C"]}
