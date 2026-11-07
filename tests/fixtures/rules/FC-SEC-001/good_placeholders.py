"""FC-SEC-001 good: placeholder keys are reported as info only."""

from openai import OpenAI

local = OpenAI(base_url="http://localhost:8000/v1", api_key="EMPTY", timeout=30.0)
EXAMPLE_KEY = "bc_live_your_key_here"
