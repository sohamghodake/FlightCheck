"""FC-BC-005 good: budgets at or below 32 000, or not literal."""

import os

from openai import OpenAI

client = OpenAI(
    base_url="https://bharatcode.ai/api/model/v1",
    api_key=os.environ["BHARATCODE_API_KEY"],
)


def at_cap(messages):
    return client.responses.create(
        model="qwen-3.8-27b",
        input=messages,
        store=False,
        max_output_tokens=32000,
    )


def small(messages):
    return client.chat.completions.create(
        model="qwen-3.8-27b",
        messages=messages,
        max_completion_tokens=4000,
    )


def configured(messages, budget):
    return client.chat.completions.create(
        model="qwen-3.8-27b",
        messages=messages,
        max_tokens=budget,
    )
