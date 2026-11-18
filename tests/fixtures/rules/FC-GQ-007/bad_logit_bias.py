"""FC-GQ-007 bad: logit_bias on Groq Chat Completions (400)."""

# INTENTIONALLY FLAWED FIXTURE: logit_bias is rejected by Groq.
import groq
from groq import Groq

client = Groq(timeout=60.0)


def ask(messages):
    try:
        return client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=messages,
            logit_bias={"50256": -100},
        )
    except groq.APIError:
        return None
