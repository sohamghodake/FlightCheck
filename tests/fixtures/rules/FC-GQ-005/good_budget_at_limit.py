"""FC-GQ-005 good: max_completion_tokens exactly at GPT-OSS 20B's 65,536."""

import groq
from groq import Groq

client = Groq(timeout=60.0)


def ask(messages):
    try:
        return client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=messages,
            max_completion_tokens=65536,
        )
    except groq.APIError:
        return None
