"""FC-GQ-001 bad: store true in a raw JSON body posted to Groq's /responses."""

# INTENTIONALLY FLAWED FIXTURE: store=True is sent on purpose.
import os

import httpx


def ask(messages):
    try:
        return httpx.post(
            "https://api.groq.com/openai/v1/responses",
            headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"},
            json={"model": "openai/gpt-oss-20b", "input": messages, "store": True},
            timeout=60.0,
        )
    except httpx.HTTPError:
        return None
