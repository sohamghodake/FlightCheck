"""FC-GQ-007 bad: logprobs and top_logprobs on Groq Chat Completions (400)."""

# INTENTIONALLY FLAWED FIXTURE: logprobs is rejected by Groq.
import groq
from groq import Groq

client = Groq(timeout=60.0)


def ask(messages):
    try:
        return client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=messages,
            logprobs=True,
            top_logprobs=3,
        )
    except groq.APIError:
        return None
