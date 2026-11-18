"""FC-GQ-010 bad: the deprecated functions/function_call."""

# INTENTIONALLY FLAWED FIXTURE: functions/function_call are deprecated on Groq.
import groq
from groq import Groq

client = Groq(timeout=60.0)


def ask(messages):
    try:
        return client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=messages,
            functions=[{"name": "lookup", "parameters": {"type": "object"}}],
            function_call="auto",
        )
    except groq.APIError:
        return None
