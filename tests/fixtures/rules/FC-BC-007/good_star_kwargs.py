"""FC-BC-007 good: **params may set store, so nothing is claimed."""

import os

from openai import OpenAI

client = OpenAI(
    base_url="https://bharatcode.ai/api/model/v1",
    api_key=os.environ["BHARATCODE_API_KEY"],
)


def ask(messages, **params):
    return client.responses.create(model="qwen-3.8-27b", input=messages, **params)
