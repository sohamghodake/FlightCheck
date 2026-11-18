import os

from openai import OpenAI

client = OpenAI(api_key=os.getenv("GROQ_API_KEY"))
answer = client.chat.completions.create(model="some-model", messages=[])
