"""FC-GQ-015 bad: the groq SDK given the OpenAI-style base URL (double /openai/v1)."""

# INTENTIONALLY FLAWED FIXTURE: the groq SDK adds /openai/v1 itself.
from groq import Groq

client = Groq(base_url="https://api.groq.com/openai/v1", timeout=60.0)
