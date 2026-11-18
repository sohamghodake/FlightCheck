"""FC-GQ-017 good: Groq's /responses through the OpenAI SDK."""

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
        )
    except openai.APIError:
        return None
