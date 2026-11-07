"""FC-GEN-002 good: status is checked before output_text is used."""

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
    if resp.status == "incomplete":
        raise RuntimeError(f"truncated: {resp.incomplete_details}")
    return resp.output_text
