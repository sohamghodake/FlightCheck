"""FC-GEN-005 bad: an OpenAI client without an explicit timeout."""

# INTENTIONALLY FLAWED FIXTURE: the client relies on the 10-minute SDK default.
import os

from openai import OpenAI

client = OpenAI(
    base_url=os.environ["BHARATCODE_BASE_URL"],
    api_key=os.environ["BHARATCODE_API_KEY"],
)
