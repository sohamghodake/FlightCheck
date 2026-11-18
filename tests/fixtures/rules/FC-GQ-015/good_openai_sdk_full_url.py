"""FC-GQ-015 good: the OpenAI SDK with Groq's full OpenAI-compatible base URL."""

import os

from openai import OpenAI

client = OpenAI(
    base_url="https://api.groq.com/openai/v1/",
    api_key=os.environ["GROQ_API_KEY"],
    timeout=60.0,
)
