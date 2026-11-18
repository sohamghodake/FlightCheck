"""FC-GQ-015 good: an httpx client on Groq's host posting to the full /openai/v1 path."""

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
            "/openai/v1/chat/completions",
            json={"model": "openai/gpt-oss-20b", "messages": messages},
        )
    except httpx.HTTPError:
        return None
