"""FC-BC-002 bad: previous_response_id from a variable (confidence medium)."""

import os

from openai import OpenAI

client = OpenAI(
    base_url="https://bharatcode.ai/api/model/v1",
    api_key=os.environ["BHARATCODE_API_KEY"],
)


def follow_up(messages, last_id):
    return client.responses.create(
        model="qwen-3.8-27b",
        input=messages,
        store=False,
        previous_response_id=last_id,
    )
