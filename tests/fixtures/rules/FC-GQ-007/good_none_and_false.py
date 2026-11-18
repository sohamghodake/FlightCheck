"""FC-GQ-007 good: logprobs=False and logit_bias=None are not flagged ([UNVERIFIED] effect)."""

import groq
from groq import Groq

client = Groq(timeout=60.0)


def ask(messages):
    try:
        return client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=messages,
            logprobs=False,
            logit_bias=None,
        )
    except groq.APIError:
        return None
