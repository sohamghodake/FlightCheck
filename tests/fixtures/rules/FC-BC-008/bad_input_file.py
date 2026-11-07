"""FC-BC-008 bad: input_file content item in a literal input."""

import os

from openai import OpenAI

client = OpenAI(
    base_url="https://bharatcode.ai/api/model/v1",
    api_key=os.environ["BHARATCODE_API_KEY"],
)


def summarise():
    return client.responses.create(
        model="qwen-3.8-27b",
        store=False,
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "Summarise this file."},
                    {"type": "input_file", "file_id": "file-abc123"},
                ],
            }
        ],
    )
