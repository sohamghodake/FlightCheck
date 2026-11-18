"""FC-GQ-002 good: history replayed in input; previous_response_id=None is not sent."""

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
            previous_response_id=None,
        )
    except openai.APIError:
        return None
