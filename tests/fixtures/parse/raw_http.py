import httpx2
import requests

BASE = "https://bharatcode.ai/api/model/v1"


def raw_responses(payload_input):
    return httpx2.post(
        f"{BASE}/responses",
        json={"model": "qwen-3.8-27b", "input": payload_input, "stream": False},
    )


def raw_chat(messages):
    return requests.post(BASE + "/chat/completions", json={"model": "qwen-3.8-27b"})


def not_an_api_call():
    return requests.post("https://example.com/upload", json={})
