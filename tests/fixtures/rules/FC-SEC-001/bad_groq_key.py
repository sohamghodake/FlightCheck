"""FC-SEC-001 bad: a Groq-prefixed key literal passed to the groq client."""

# INTENTIONALLY FLAWED FIXTURE: the key below is fake but hard-coded on purpose.
from groq import Groq

client = Groq(api_key="gsk_FAKE0000000000000000000000", timeout=60.0)
