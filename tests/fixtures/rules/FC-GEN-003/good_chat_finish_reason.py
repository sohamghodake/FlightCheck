"""FC-GEN-003 good: a Chat stream loop that checks finish_reason."""

import os

from openai import OpenAI

client = OpenAI(
    base_url=os.environ["BHARATCODE_BASE_URL"],
    api_key=os.environ["BHARATCODE_API_KEY"],
    timeout=300.0,
)


def stream_answer(question):
    parts = []
    try:
        stream = client.chat.completions.create(
            model="qwen-3.8-27b",
            messages=[{"role": "user", "content": question}],
            stream=True,
        )
        for chunk in stream:
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            parts.append(choice.delta.content or "")
            if choice.finish_reason == "length":
                parts.append(" [truncated]")
    except Exception:
        return None
    return "".join(parts)
