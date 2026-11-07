"""FC-SEC-001 bad: a short non-placeholder literal passed as api_key=."""

# INTENTIONALLY FLAWED FIXTURE: the key below is fake but hard-coded on purpose.
from openai import OpenAI

client = OpenAI(
    base_url="https://bharatcode.ai/api/model/v1",
    api_key="abc12345",
    timeout=300.0,
)
