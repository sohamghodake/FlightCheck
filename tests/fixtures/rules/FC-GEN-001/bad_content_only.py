"""FC-GEN-001 bad: Chat reply read without checking finish_reason."""

# INTENTIONALLY FLAWED FIXTURE: finish_reason is never checked.
import os

from openai import OpenAI

client = OpenAI(
    base_url=os.environ["BHARATCODE_BASE_URL"],
    api_key=os.environ["BHARATCODE_API_KEY"],
    timeout=300.0,
)


def ask(question):
    try:
        resp = client.chat.completions.create(
            model="qwen-3.8-27b",
            messages=[{"role": "user", "content": question}],
        )
    except Exception:
        return None
    return resp.choices[0].message.content
