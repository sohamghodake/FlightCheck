import os

import openai
from openai import OpenAI


class Agent:
    def __init__(self):
        self.client = OpenAI(base_url=os.getenv("BHARATCODE_BASE_URL"))

    def run(self, prompt):
        try:
            return self.client.responses.create(model="qwen-3.8-27b", input=prompt)
        except (openai.APIError, TimeoutError):
            return None

    def run_unprotected(self, prompt):
        return self.client.responses.stream(model="qwen-3.8-27b", input=prompt)
