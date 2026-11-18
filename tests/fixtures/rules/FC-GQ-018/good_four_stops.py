"""FC-GQ-018 good: four stop sequences."""

import groq
from groq import Groq

client = Groq(timeout=60.0)


def ask(messages):
    try:
        return client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=messages,
            stop=["END", "###", "STOP", "--"],
        )
    except groq.APIError:
        return None
