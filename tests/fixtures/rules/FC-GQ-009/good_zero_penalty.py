"""FC-GQ-009 good: a zero penalty is the default and changes nothing."""

import groq
from groq import Groq

client = Groq(timeout=60.0)


def ask(messages):
    try:
        return client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=messages,
            frequency_penalty=0,
        )
    except groq.APIError:
        return None
