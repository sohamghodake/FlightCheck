"""FC-GEN-004 good: provider errors are caught around the call."""

import os

import openai
from openai import OpenAI

client = OpenAI(
    base_url=os.environ["BHARATCODE_BASE_URL"],
    api_key=os.environ["BHARATCODE_API_KEY"],
    timeout=300.0,
)


def ask(messages):
    try:
        return client.chat.completions.create(model="qwen-3.8-27b", messages=messages)
    except openai.APIStatusError as exc:
        raise RuntimeError(f"request {exc.request_id} failed") from exc
