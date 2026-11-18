"""FC-GQ-015 bad: the OpenAI SDK pointed at Groq's host without /openai/v1."""

# INTENTIONALLY FLAWED FIXTURE: the OpenAI SDK needs the full /openai/v1 base URL.
import os

from openai import OpenAI

client = OpenAI(
    base_url="https://api.groq.com",
    api_key=os.environ["GROQ_API_KEY"],
    timeout=60.0,
)
