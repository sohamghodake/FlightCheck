import os

from openai import OpenAI as C

client = C(api_key=os.environ["BHARATCODE_API_KEY"])


def chat(messages, flag):
    return client.chat.completions.create(model="qwen-3.8-27b", messages=messages, store=flag)
