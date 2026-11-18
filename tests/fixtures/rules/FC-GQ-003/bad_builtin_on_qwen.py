"""FC-GQ-003 bad: Groq's built-in browser_search on a non-GPT-OSS model."""

# INTENTIONALLY FLAWED FIXTURE: built-in tools need a GPT-OSS model.
import groq
from groq import Groq

client = Groq(timeout=60.0)


def ask(messages):
    try:
        return client.chat.completions.create(
            model="qwen/qwen3.8-27b",
            messages=messages,
            tools=[{"type": "browser_search"}],
        )
    except groq.APIError:
        return None
