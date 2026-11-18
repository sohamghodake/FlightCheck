"""FC-GQ-010 good: max_completion_tokens and tools."""

import groq
from groq import Groq

client = Groq(timeout=60.0)


def ask(messages):
    try:
        return client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=messages,
            max_completion_tokens=512,
            tool_choice="auto",
            tools=[{"type": "function", "function": {"name": "lookup"}}],
        )
    except groq.APIError:
        return None
