"""FC-GQ-002 bad: a literal previous_response_id on Groq's stateless /responses."""

# INTENTIONALLY FLAWED FIXTURE: previous_response_id is sent on purpose.
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
            previous_response_id="resp_123",
        )
    except openai.APIError:
        return None
