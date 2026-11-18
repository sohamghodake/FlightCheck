import os

from openai import OpenAI

client = OpenAI(base_url="https://llm.example.net/v1", api_key=os.environ["LLM_API_KEY"])
answer = client.chat.completions.create(model="some-model", messages=[])
