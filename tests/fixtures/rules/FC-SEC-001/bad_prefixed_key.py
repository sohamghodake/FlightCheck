"""FC-SEC-001 bad: a BharatCode-prefixed key literal passed to the client."""

# INTENTIONALLY FLAWED FIXTURE: the key below is fake but hard-coded on purpose.
from openai import OpenAI

client = OpenAI(
    base_url="https://bharatcode.ai/api/model/v1",
    api_key="bc_live_FAKE0000000000000000",
    timeout=300.0,
)
