"""FC-BC-003 good: tools built dynamically are not inspected."""

import os

from openai import OpenAI

client = OpenAI(
    base_url="https://bharatcode.ai/api/model/v1",
    api_key=os.environ["BHARATCODE_API_KEY"],
)


def build_tools(names):
    return [{"type": "function", "name": name} for name in names]


def run(messages, names):
    return client.responses.create(
        model="qwen-3.8-27b",
        input=messages,
        store=False,
        tools=build_tools(names),
    )
