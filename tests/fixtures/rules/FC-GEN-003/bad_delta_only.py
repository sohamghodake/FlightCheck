"""FC-GEN-003 bad: a Responses stream loop that only reads text deltas."""

# INTENTIONALLY FLAWED FIXTURE: terminal stream events are never handled.
import os

from openai import OpenAI

client = OpenAI(
    base_url=os.environ["BHARATCODE_BASE_URL"],
    api_key=os.environ["BHARATCODE_API_KEY"],
    timeout=300.0,
)


def stream_answer(question):
    try:
        stream = client.responses.create(
            model="qwen-3.8-27b", input=question, store=False, stream=True
        )
        for event in stream:
            if event.type == "response.output_text.delta":
                print(event.delta, end="")
    except Exception:
        return
