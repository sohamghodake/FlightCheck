"""FC-GEN-004 bad (low confidence): the decorator might retry, but nothing is caught here."""

# INTENTIONALLY FLAWED FIXTURE: no try/except around the call.
import os

from openai import OpenAI

client = OpenAI(
    base_url=os.environ["BHARATCODE_BASE_URL"],
    api_key=os.environ["BHARATCODE_API_KEY"],
    timeout=300.0,
)


def retry_on_busy(fn):
    return fn


@retry_on_busy
def ask(messages):
    return client.chat.completions.create(model="qwen-3.8-27b", messages=messages)
