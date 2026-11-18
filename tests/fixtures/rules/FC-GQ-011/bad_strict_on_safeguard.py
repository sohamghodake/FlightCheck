"""FC-GQ-011 bad: strict: true on a best-effort-only model."""

# INTENTIONALLY FLAWED FIXTURE: strict mode is not supported on this model.
import groq
from groq import Groq

client = Groq(timeout=60.0)


def ask(messages):
    try:
        return client.chat.completions.create(
            model="openai/gpt-oss-safeguard-20b",
            messages=messages,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "answer",
                    "strict": True,
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
