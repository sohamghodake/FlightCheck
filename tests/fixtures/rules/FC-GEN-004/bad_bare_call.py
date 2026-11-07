"""FC-GEN-004 bad: an API call with no error handling."""

# INTENTIONALLY FLAWED FIXTURE: no try/except around the call.
import os

from openai import OpenAI

client = OpenAI(
    base_url=os.environ["BHARATCODE_BASE_URL"],
    api_key=os.environ["BHARATCODE_API_KEY"],
    timeout=300.0,
)


def ask(messages):
    return client.chat.completions.create(model="qwen-3.8-27b", messages=messages)
