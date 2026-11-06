import os
from os import environ, getenv

KEY = os.environ["BHARATCODE_API_KEY"]
URL = os.environ.get("BHARATCODE_BASE_URL", "https://bharatcode.ai/api/model/v1")
MODEL = os.getenv("LLM_MODEL")
REGION = environ.get("REGION")
DEBUG = getenv("DEBUG", "0")
