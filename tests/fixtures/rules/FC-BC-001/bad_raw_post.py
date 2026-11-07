"""FC-BC-001 bad: raw HTTP post to /responses with "store": True in the JSON body."""

import os

import httpx2


def ask(text):
    return httpx2.post(
        "https://bharatcode.ai/api/model/v1/responses",
        headers={"Authorization": "Bearer " + os.environ["BHARATCODE_API_KEY"]},
        json={"model": "qwen-3.8-27b", "input": text, "store": True},
    )
