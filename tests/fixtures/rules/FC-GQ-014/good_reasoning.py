"""FC-GQ-014 good: a documented effort on GPT-OSS, no reasoning_format."""

import groq
from groq import Groq

client = Groq(timeout=60.0)


def ask(messages):
    try:
        return client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=messages,
            reasoning_effort="low",
            include_reasoning=False,
        )
    except groq.APIError:
        return None
