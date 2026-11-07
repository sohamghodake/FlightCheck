"""FC-BC-006 good: a documented model."""

import os

from openai import OpenAI

client = OpenAI(
    base_url="https://bharatcode.ai/api/model/v1",
    api_key=os.environ["BHARATCODE_API_KEY"],
)


def ask(messages):
    return client.chat.completions.create(model="qwen-3.8-27b", messages=messages)
