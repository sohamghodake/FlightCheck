"""FC-GQ-019 good: stream comes from a variable, so it may be True (not flagged)."""

import groq
from groq import Groq

client = Groq(timeout=60.0)


def ask(messages, stream):
    try:
        return client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=messages,
            stream=stream,
            stream_options={"include_usage": True},
        )
    except groq.APIError:
        return None
