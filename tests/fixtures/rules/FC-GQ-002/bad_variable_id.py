"""FC-GQ-002 bad: previous_response_id from a variable (medium confidence)."""

# INTENTIONALLY FLAWED FIXTURE: previous_response_id is sent on purpose.
import os

import openai
from openai import OpenAI

client = OpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=os.environ["GROQ_API_KEY"],
    timeout=60.0,
)


def ask(messages, last_id):
    try:
        return client.responses.create(
            model="openai/gpt-oss-20b",
            input=messages,
            previous_response_id=last_id,
        )
    except openai.APIError:
        return None
