"""FC-BC-008 good: text and image input are supported."""

import os

from openai import OpenAI

client = OpenAI(
    base_url="https://bharatcode.ai/api/model/v1",
    api_key=os.environ["BHARATCODE_API_KEY"],
)


def describe(url):
    return client.responses.create(
        model="qwen-3.8-27b",
        store=False,
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "Describe this image."},
                    {"type": "input_image", "image_url": url},
                ],
            }
        ],
    )
