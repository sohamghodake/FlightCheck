"""FC-SEC-001 bad: a key literal in a settings dict and a module constant."""

# INTENTIONALLY FLAWED FIXTURE: the keys below are fake but hard-coded on purpose.
SETTINGS = {"api_key": "abc", "model": "qwen-3.8-27b"}
OPENAI_API_KEY = "fake-key-value-0000"
