import os

import openai

client = openai.OpenAI(base_url=os.environ["GROQ_BASE_URL"])
answer = client.responses.create(model="openai/gpt-oss-20b", input="hi")
