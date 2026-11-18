"""FC-GQ-014 bad: reasoning_effort none on GPT-OSS (400)."""

# INTENTIONALLY FLAWED FIXTURE: GPT-OSS does not accept reasoning_effort none.
import groq
from groq import Groq

client = Groq(timeout=60.0)


def ask(messages):
    try:
        return client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=messages,
            reasoning_effort="none",
        )
    except groq.APIError:
        return None
