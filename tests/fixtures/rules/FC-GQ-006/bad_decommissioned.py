"""FC-GQ-006 bad: a decommissioned Groq model ID."""

# INTENTIONALLY FLAWED FIXTURE: the model was decommissioned.
import groq
from groq import Groq

client = Groq(timeout=60.0)


def ask(messages):
    try:
        return client.chat.completions.create(
            model="llama3-70b-8192",
            messages=messages,
        )
    except groq.APIError:
        return None
