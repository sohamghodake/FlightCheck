"""FC-GQ-004 good: only documented Responses parameters."""

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
            instructions="Be brief.",
            max_output_tokens=1024,
        )
    except openai.APIError:
        return None
