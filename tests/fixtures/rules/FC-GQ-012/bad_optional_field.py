"""FC-GQ-012 bad: a strict text.format schema with a property left out of required."""

# INTENTIONALLY FLAWED FIXTURE: the strict schema is not strict-compatible.
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
            text={
                "format": {
                    "type": "json_schema",
                    "name": "answer",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {"answer": {"type": "string"}, "note": {"type": "string"}},
                        "required": ["answer"],
                        "additionalProperties": False,
                    },
                },
            },
        )
    except openai.APIError:
        return None
