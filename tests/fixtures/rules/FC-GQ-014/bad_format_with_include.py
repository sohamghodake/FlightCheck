"""FC-GQ-014 bad: reasoning_format together with include_reasoning."""

# INTENTIONALLY FLAWED FIXTURE: reasoning_format and include_reasoning are mutually exclusive.
import groq
from groq import Groq

client = Groq(timeout=60.0)


def ask(messages):
    try:
        return client.chat.completions.create(
            model="qwen/qwen3.8-27b",
            messages=messages,
            reasoning_format="parsed",
            include_reasoning=True,
        )
    except groq.APIError:
        return None
