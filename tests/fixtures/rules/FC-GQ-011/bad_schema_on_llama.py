"""FC-GQ-011 bad: json_schema with a model that supports JSON object mode only."""

# INTENTIONALLY FLAWED FIXTURE: json_schema is not supported on this model.
import groq
from groq import Groq

client = Groq(timeout=60.0)


def ask(messages):
    try:
        return client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "answer",
                    "strict": False,
                    "schema": {
                        "type": "object",
                        "properties": {"answer": {"type": "string"}},
                        "required": ["answer"],
                        "additionalProperties": False,
                    },
                },
            },
        )
    except groq.APIError:
        return None
