"""FC-GQ-005 bad: max_completion_tokens above GPT-OSS 20B's 65,536."""

# INTENTIONALLY FLAWED FIXTURE: the budget exceeds the model's maximum.
import groq
from groq import Groq

client = Groq(timeout=60.0)


def ask(messages):
    try:
        return client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=messages,
            max_completion_tokens=70000,
        )
    except groq.APIError:
        return None
