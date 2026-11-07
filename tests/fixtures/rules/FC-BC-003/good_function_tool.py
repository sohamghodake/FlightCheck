"""FC-BC-003 good: an application-defined function tool."""

import os

from openai import OpenAI

client = OpenAI(
    base_url="https://bharatcode.ai/api/model/v1",
    api_key=os.environ["BHARATCODE_API_KEY"],
)


WEATHER_TOOL = {
    "type": "function",
    "name": "get_weather",
    "description": "Current weather for a city.",
    "parameters": {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
        "additionalProperties": False,
    },
    "strict": True,
}


def weather(messages):
    return client.responses.create(
        model="qwen-3.8-27b",
        input=messages,
        store=False,
        tools=[WEATHER_TOOL],
    )
