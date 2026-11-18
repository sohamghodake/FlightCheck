"""FC-GQ-012 good: best-effort mode (strict omitted) allows optional fields."""

import groq
from groq import Groq

client = Groq(timeout=60.0)


def ask(messages):
    try:
        return client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=messages,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "answer",
                    "schema": {"type": "object", "properties": {"answer": {"type": "string"}}},
                },
            },
        )
    except groq.APIError:
        return None
