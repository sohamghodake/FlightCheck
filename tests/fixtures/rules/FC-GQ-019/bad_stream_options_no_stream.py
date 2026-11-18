"""FC-GQ-019 bad: stream_options without stream=True."""

# INTENTIONALLY FLAWED FIXTURE: stream_options is set without streaming.
import groq
from groq import Groq

client = Groq(timeout=60.0)


def ask(messages):
    try:
        return client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=messages,
            stream_options={"include_usage": True},
        )
    except groq.APIError:
        return None
