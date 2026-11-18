"""FC-GQ-019 good: stream_options with stream=True."""

import groq
from groq import Groq

client = Groq(timeout=60.0)


def ask(messages):
    try:
        return client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=messages,
            stream=True,
            stream_options={"include_usage": True},
        )
    except groq.APIError:
        return None
