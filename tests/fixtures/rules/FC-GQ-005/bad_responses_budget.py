"""FC-GQ-005 bad: max_output_tokens above Llama 3.3 70B's 32,768."""

# INTENTIONALLY FLAWED FIXTURE: the budget exceeds the model's maximum.
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
            model="llama-3.3-70b-versatile",
            input=messages,
            max_output_tokens=40000,
        )
    except openai.APIError:
        return None
