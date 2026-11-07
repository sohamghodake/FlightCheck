"""FC-GEN-003 bad: a Chat stream loop that never checks finish_reason."""

# INTENTIONALLY FLAWED FIXTURE: finish_reason is never checked in the stream loop.
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
        for chunk in client.chat.completions.create(
            model="qwen-3.8-27b",
            messages=[{"role": "user", "content": question}],
            stream=True,
        ):
            if chunk.choices:
                parts.append(chunk.choices[0].delta.content or "")
    except Exception:
        return None
    return "".join(parts)
