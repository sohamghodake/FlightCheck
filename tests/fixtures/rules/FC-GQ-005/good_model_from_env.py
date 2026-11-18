"""FC-GQ-005 good: the model is not a literal, so its limit is unknown."""

import os

import groq
from groq import Groq

client = Groq(timeout=60.0)

MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b")


def ask(messages):
    try:
        return client.chat.completions.create(
            model=MODEL,
            messages=messages,
            max_completion_tokens=200000,
        )
    except groq.APIError:
        return None
