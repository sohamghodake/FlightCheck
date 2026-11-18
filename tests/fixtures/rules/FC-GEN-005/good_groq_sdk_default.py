"""FC-GEN-005 good: a groq client without timeout= gets an info note (60 s SDK default)."""

import os

from groq import Groq

client = Groq(api_key=os.environ["GROQ_API_KEY"])
