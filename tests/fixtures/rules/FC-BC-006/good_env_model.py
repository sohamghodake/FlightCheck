"""FC-BC-006 good: the model comes from the environment (not literal)."""

import os

from openai import OpenAI

client = OpenAI(
    base_url="https://bharatcode.ai/api/model/v1",
    api_key=os.environ["BHARATCODE_API_KEY"],
)


MODEL = os.environ.get("BHARATCODE_MODEL", "qwen-3.8-27b")


def ask(messages):
    return client.chat.completions.create(model=MODEL, messages=messages)
