"""FC-GQ-014 bad: reasoning effort xhigh on GPT-OSS via /responses (400)."""

# INTENTIONALLY FLAWED FIXTURE: GPT-OSS does not accept reasoning effort xhigh.
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
            reasoning={"effort": "xhigh"},
        )
    except openai.APIError:
        return None
