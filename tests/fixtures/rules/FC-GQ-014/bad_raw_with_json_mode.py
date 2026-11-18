"""FC-GQ-014 bad: reasoning_format raw with JSON object mode (400)."""

# INTENTIONALLY FLAWED FIXTURE: raw reasoning is rejected with JSON mode.
import groq
from groq import Groq

client = Groq(timeout=60.0)


def ask(messages):
    try:
        return client.chat.completions.create(
            model="qwen/qwen3.8-27b",
            messages=messages,
            reasoning_format="raw",
            response_format={"type": "json_object"},
        )
    except groq.APIError:
        return None
