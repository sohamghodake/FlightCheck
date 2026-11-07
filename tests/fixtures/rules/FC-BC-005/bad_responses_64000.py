"""FC-BC-005 bad: max_output_tokens=64000 on /responses."""

import os

from openai import OpenAI

client = OpenAI(
    base_url="https://bharatcode.ai/api/model/v1",
    api_key=os.environ["BHARATCODE_API_KEY"],
)


def long_answer(messages):
    return client.responses.create(
        model="qwen-3.8-27b",
        input=messages,
        store=False,
        max_output_tokens=64000,
    )
