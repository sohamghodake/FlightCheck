"""FC-GQ-003 bad: an OpenAI hosted tool type Groq does not document."""

# INTENTIONALLY FLAWED FIXTURE: web_search is not a Groq tool type.
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
            tools=[{"type": "web_search"}],
        )
    except openai.APIError:
        return None
