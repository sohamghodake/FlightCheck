"""FC-GEN-005 good: the timeout is set per request with with_options(timeout=...)."""

import os

from openai import OpenAI

client = OpenAI(
    base_url=os.environ["BHARATCODE_BASE_URL"],
    api_key=os.environ["BHARATCODE_API_KEY"],
)


def ask(messages):
    try:
        return client.with_options(timeout=60.0).chat.completions.create(
            model="qwen-3.8-27b", messages=messages
        )
    except Exception:
        return None
