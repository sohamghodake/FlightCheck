"""FC-GQ-015 good: the groq SDK with its default base URL."""

from groq import Groq

client = Groq(timeout=60.0)
