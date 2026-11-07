"""FC-BC-003 bad: hosted file_search tool via a module-level list (one hop)."""

import os

from openai import OpenAI

client = OpenAI(
    base_url="https://bharatcode.ai/api/model/v1",
    api_key=os.environ["BHARATCODE_API_KEY"],
)


TOOLS = [{"type": "file_search", "vector_store_ids": ["vs_1"]}]


def search(messages):
    return client.responses.create(
        model="qwen-3.8-27b",
        input=messages,
        store=False,
        tools=TOOLS,
    )
