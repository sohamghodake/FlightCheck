"""FC-BC-003 bad: hosted web_search tool in a literal list."""

import os

from openai import OpenAI

client = OpenAI(
    base_url="https://bharatcode.ai/api/model/v1",
    api_key=os.environ["BHARATCODE_API_KEY"],
)


def search(messages):
    return client.responses.create(
        model="qwen-3.8-27b",
        input=messages,
        store=False,
        tools=[{"type": "web_search"}],
    )
