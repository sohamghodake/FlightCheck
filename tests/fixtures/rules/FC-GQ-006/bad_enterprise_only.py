"""FC-GQ-006 bad: a Llama model shut down for free/developer tiers (Enterprise only)."""

# INTENTIONALLY FLAWED FIXTURE: the model is Enterprise-only since 16 Aug 2026.
import groq
from groq import Groq

client = Groq(timeout=60.0)


def ask(messages):
    try:
        return client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=messages,
        )
    except groq.APIError:
        return None
