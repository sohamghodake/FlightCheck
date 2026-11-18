"""FC-GQ-003 good: a function tool and a remote MCP tool (both documented)."""

import os

import openai
from openai import OpenAI

client = OpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=os.environ["GROQ_API_KEY"],
    timeout=60.0,
)


def ask(messages):
    try:
        return client.responses.create(
            model="openai/gpt-oss-20b",
            input=messages,
            tools=[
                {"type": "function", "name": "get_weather", "parameters": {"type": "object"}},
                {"type": "mcp", "server_label": "docs", "server_url": "https://mcp.example.com"},
            ],
        )
    except openai.APIError:
        return None
