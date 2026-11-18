"""FC-GQ-003 good: browser_search and code_interpreter on a GPT-OSS model."""

import groq
from groq import Groq

client = Groq(timeout=60.0)


def ask(messages):
    try:
        return client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=messages,
            tools=[{"type": "browser_search"}, {"type": "code_interpreter"}],
        )
    except groq.APIError:
        return None
