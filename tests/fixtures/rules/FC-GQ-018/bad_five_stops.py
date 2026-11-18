"""FC-GQ-018 bad: five stop sequences (Groq accepts up to four)."""

# INTENTIONALLY FLAWED FIXTURE: too many stop sequences.
import groq
from groq import Groq

client = Groq(timeout=60.0)


def ask(messages):
    try:
        return client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=messages,
            stop=["END", "###", "STOP", "--", "DONE"],
        )
    except groq.APIError:
        return None
