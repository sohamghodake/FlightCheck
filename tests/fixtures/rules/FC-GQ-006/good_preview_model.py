"""FC-GQ-006 good: a preview model gets an info note only."""

import groq
from groq import Groq

client = Groq(timeout=60.0)


def ask(messages):
    try:
        return client.chat.completions.create(
            model="qwen/qwen3.8-27b",
            messages=messages,
        )
    except groq.APIError:
        return None
