"""FC-GQ-013 bad: JSON object mode with literal messages that never mention JSON."""

# INTENTIONALLY FLAWED FIXTURE: the prompt does not ask for JSON.
import groq
from groq import Groq

client = Groq(timeout=60.0)


def ask():
    try:
        return client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[{"role": "user", "content": "List three colours."}],
            response_format={"type": "json_object"},
        )
    except groq.APIError:
        return None
