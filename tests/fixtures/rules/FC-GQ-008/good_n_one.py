"""FC-GQ-008 good: n=1."""

import groq
from groq import Groq

client = Groq(timeout=60.0)


def ask(messages):
    try:
        return client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=messages,
            n=1,
        )
    except groq.APIError:
        return None
