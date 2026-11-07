"""FC-BC-006 bad: a model outside the documented catalogue on a BharatCode client."""

import os

from openai import OpenAI

client = OpenAI(
    base_url="https://bharatcode.ai/api/model/v1",
    api_key=os.environ["BHARATCODE_API_KEY"],
)


def ask(messages):
    return client.chat.completions.create(model="gpt-4o", messages=messages)
