"""FC-BC-004 good: background absent or False."""

import os

from openai import OpenAI

client = OpenAI(
    base_url="https://bharatcode.ai/api/model/v1",
    api_key=os.environ["BHARATCODE_API_KEY"],
)


def run(messages):
    return client.responses.create(model="qwen-3.8-27b", input=messages, store=False)


def run_explicit(messages):
    return client.responses.create(
        model="qwen-3.8-27b",
        input=messages,
        store=False,
        background=False,
    )
