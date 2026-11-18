"""FC-GQ-013 good: messages are not a literal, so the prompt cannot be judged."""

import groq
from groq import Groq

client = Groq(timeout=60.0)


def ask(messages):
    try:
        return client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=messages,
            response_format={"type": "json_object"},
        )
    except groq.APIError:
        return None
