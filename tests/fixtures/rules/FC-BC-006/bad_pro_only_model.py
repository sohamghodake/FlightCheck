"""FC-BC-006 info: deepseek-v4.1-flash requires a Pro plan."""

import os

from openai import OpenAI

client = OpenAI(
    base_url="https://bharatcode.ai/api/model/v1",
    api_key=os.environ["BHARATCODE_API_KEY"],
)


def ask(messages):
    return client.chat.completions.create(model="deepseek-v4.1-flash", messages=messages)
