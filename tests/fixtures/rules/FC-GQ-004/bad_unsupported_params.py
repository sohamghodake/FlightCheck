"""FC-GQ-004 bad: include and prompt_cache_key on Groq's /responses."""

# INTENTIONALLY FLAWED FIXTURE: unsupported Responses parameters are sent on purpose.
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
            include=["reasoning.encrypted_content"],
            prompt_cache_key="chat-1",
        )
    except openai.APIError:
        return None
