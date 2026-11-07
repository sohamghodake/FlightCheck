"""FC-GEN-003 good: completed, incomplete and failed events are handled."""

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
        stream = client.responses.create(
            model="qwen-3.8-27b", input=question, store=False, stream=True
        )
        for event in stream:
            if event.type == "response.output_text.delta":
                parts.append(event.delta)
            elif event.type == "response.completed":
                return "".join(parts)
            elif event.type == "response.incomplete":
                return "".join(parts) + " [truncated]"
            elif event.type == "response.failed":
                raise RuntimeError("stream failed")
    except Exception:
        return None
    raise RuntimeError("stream ended without a terminal event")
