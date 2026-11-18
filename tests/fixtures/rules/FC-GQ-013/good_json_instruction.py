"""FC-GQ-013 good: the system message asks for JSON."""

import groq
from groq import Groq

client = Groq(timeout=60.0)


def ask():
    try:
        return client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[
                {"role": "system", "content": "Reply with a JSON object."},
                {"role": "user", "content": "List three colours."},
            ],
            response_format={"type": "json_object"},
        )
    except groq.APIError:
        return None
