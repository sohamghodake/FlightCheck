"""FC-GQ-001 bad: store=True on Groq's /responses (OpenAI SDK)."""

# INTENTIONALLY FLAWED FIXTURE: store=True is sent on purpose.
import os

import openai
from openai import OpenAI

client = OpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=os.environ["GROQ_API_KEY"],
    timeout=60.0,
)


def ask(messages):
    try:
        return client.responses.create(
            model="openai/gpt-oss-20b",
            input=messages,
            store=True,
        )
    except openai.APIError:
        return None
