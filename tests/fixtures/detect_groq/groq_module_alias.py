import os

import groq as g

client = g.Groq(api_key=os.environ["GROQ_API_KEY"])
reply = client.chat.completions.create(model="qwen/qwen3.8-27b", messages=[])
