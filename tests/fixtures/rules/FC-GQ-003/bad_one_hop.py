"""FC-GQ-003 bad: an undocumented hosted tool via a module-level list (one hop)."""

# INTENTIONALLY FLAWED FIXTURE: file_search is not a Groq tool type.
import os

import openai
from openai import OpenAI

client = OpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=os.environ["GROQ_API_KEY"],
    timeout=60.0,
)


TOOLS = [{"type": "file_search", "vector_store_ids": ["vs_1"]}]


def ask(messages):
    try:
        return client.responses.create(
            model="openai/gpt-oss-20b",
            input=messages,
            tools=TOOLS,
        )
    except openai.APIError:
        return None
