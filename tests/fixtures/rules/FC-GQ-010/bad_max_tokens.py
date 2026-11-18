"""FC-GQ-010 bad: the deprecated max_tokens."""

# INTENTIONALLY FLAWED FIXTURE: max_tokens is deprecated on Groq.
import groq
from groq import Groq

client = Groq(timeout=60.0)


def ask(messages):
    try:
        return client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=messages,
            max_tokens=512,
        )
    except groq.APIError:
        return None
