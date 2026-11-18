"""FC-GQ-008 bad: n=2 (Groq supports only n=1)."""

# INTENTIONALLY FLAWED FIXTURE: n must be 1 on Groq.
import groq
from groq import Groq

client = Groq(timeout=60.0)


def ask(messages):
    try:
        return client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=messages,
            n=2,
        )
    except groq.APIError:
        return None
