"""FC-GQ-009 bad: presence_penalty and metadata have no effect on Groq."""

# INTENTIONALLY FLAWED FIXTURE: unsupported parameters are sent on purpose.
import groq
from groq import Groq

client = Groq(timeout=60.0)


def ask(messages):
    try:
        return client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=messages,
            presence_penalty=0.6,
            metadata={"user": "u1"},
        )
    except groq.APIError:
        return None
