"""FC-GQ-017 bad: responses.create on a groq SDK client (no such resource in v1.7.0)."""

# INTENTIONALLY FLAWED FIXTURE: the groq SDK has no responses resource.
import groq
from groq import Groq

client = Groq(timeout=60.0)


def ask(messages):
    try:
        return client.responses.create(model="openai/gpt-oss-20b", input=messages)
    except groq.APIError:
        return None
