"""FC-GQ-014 bad: reasoning_format on a GPT-OSS model."""

# INTENTIONALLY FLAWED FIXTURE: GPT-OSS does not support reasoning_format.
import groq
from groq import Groq

client = Groq(timeout=60.0)


def ask(messages):
    try:
        return client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=messages,
            reasoning_format="parsed",
        )
    except groq.APIError:
        return None
