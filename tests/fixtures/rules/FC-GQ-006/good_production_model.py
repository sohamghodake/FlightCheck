"""FC-GQ-006 good: a production model from the catalogue."""

import groq
from groq import Groq

client = Groq(timeout=60.0)


def ask(messages):
    try:
        return client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=messages,
        )
    except groq.APIError:
        return None
