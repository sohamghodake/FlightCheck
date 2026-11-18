"""FC-GQ-006 bad: a model ID Groq does not list."""

# INTENTIONALLY FLAWED FIXTURE: the model is not served by Groq.
import groq
from groq import Groq

client = Groq(timeout=60.0)


def ask(messages):
    try:
        return client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
        )
    except groq.APIError:
        return None
