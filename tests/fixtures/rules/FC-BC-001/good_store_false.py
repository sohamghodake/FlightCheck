"""FC-BC-001 good: store=False."""

import os

from openai import OpenAI

client = OpenAI(
    base_url="https://bharatcode.ai/api/model/v1",
    api_key=os.environ["BHARATCODE_API_KEY"],
)


def ask(messages):
    return client.responses.create(model="qwen-3.8-27b", input=messages, store=False)
