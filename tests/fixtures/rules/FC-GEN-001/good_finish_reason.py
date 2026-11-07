"""FC-GEN-001 good: finish_reason is checked before the reply is used."""

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
    choice = resp.choices[0]
    if choice.finish_reason == "length":
        raise RuntimeError("answer truncated")
    return choice.message.content
