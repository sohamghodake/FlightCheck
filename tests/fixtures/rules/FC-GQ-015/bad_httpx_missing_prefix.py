"""FC-GQ-015 bad: an httpx client on Groq's host posting to a path without /openai/v1."""

# INTENTIONALLY FLAWED FIXTURE: the request path misses /openai/v1.
import os

import httpx

http = httpx.Client(
    base_url="https://api.groq.com",
    headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"},
    timeout=60.0,
)


def ask(messages):
    try:
        return http.post(
            "/chat/completions",
            json={"model": "openai/gpt-oss-20b", "messages": messages},
        )
    except httpx.HTTPError:
        return None
