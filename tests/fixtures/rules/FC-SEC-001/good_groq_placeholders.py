"""FC-SEC-001 good: Groq's documented placeholder keys are reported as info only."""

from groq import Groq

client = Groq(api_key="gsk_...", timeout=60.0)
EXAMPLE_KEY = "gsk_your_secret_key_here"
