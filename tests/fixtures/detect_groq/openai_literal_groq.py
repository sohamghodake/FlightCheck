import os

from openai import OpenAI

client = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=os.environ["GROQ_API_KEY"])
answer = client.chat.completions.create(model="openai/gpt-oss-20b", messages=[])
