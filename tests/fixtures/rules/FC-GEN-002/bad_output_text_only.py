"""FC-GEN-002 bad: Responses output read without checking status."""

# INTENTIONALLY FLAWED FIXTURE: status is never checked.
import os

from openai import OpenAI

client = OpenAI(
    base_url=os.environ["BHARATCODE_BASE_URL"],
    api_key=os.environ["BHARATCODE_API_KEY"],
    timeout=300.0,
)


def ask(question):
    try:
        resp = client.responses.create(model="qwen-3.8-27b", input=question, store=False)
    except Exception:
        return None
    return resp.output_text
