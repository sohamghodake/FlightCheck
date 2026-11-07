"""Responses tool loop done right for BharatCode (clean fixture).

store=False with the history replayed, string tool outputs with the echoed call_id,
status checks, error handling and an explicit timeout.

Usage: python main.py "your question"
"""

import json
import os
import sys

import openai
from openai import OpenAI

MODEL = "qwen-3.8-27b"

TOOLS = [
    {
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
]

client = OpenAI(
    base_url=os.environ["BHARATCODE_BASE_URL"],
    api_key=os.environ["BHARATCODE_API_KEY"],
    timeout=300.0,
)


def get_weather(city: str) -> dict:
    return {"city": city, "temp_c": 31, "sky": "clear"}


def run_tool(item) -> dict:
    try:
        args = json.loads(item.arguments)
    except json.JSONDecodeError as exc:
        return {"error": f"invalid arguments: {exc}"}
    if item.name != "get_weather":
        return {"error": f"unknown tool {item.name}"}
    return get_weather(str(args.get("city", "")))


def create(history: list):
    try:
        response = client.responses.create(
            model=MODEL, input=history, tools=TOOLS, store=False, max_output_tokens=2000
        )
    except openai.APIStatusError as exc:
        raise SystemExit(f"BharatCode error {exc.status_code} (request {exc.request_id})") from exc
    except openai.APIConnectionError as exc:
        raise SystemExit(f"Could not reach BharatCode: {exc}") from exc
    if response.status == "incomplete":
        reason = response.incomplete_details.reason if response.incomplete_details else "unknown"
        raise SystemExit(f"Answer truncated ({reason}); partial output: {response.output_text!r}")
    return response


def ask(question: str) -> str:
    history: list = [{"role": "user", "content": question}]
    response = create(history)
    calls = [item for item in response.output if item.type == "function_call"]
    if calls:
        history.extend(response.output)  # replay the model's output items (store=False)
        for call in calls:
            history.append(
                {
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": json.dumps(run_tool(call)),
                }
            )
        response = create(history)
    return response.output_text


def main() -> int:
    if len(sys.argv) < 2:
        print('usage: python main.py "question"', file=sys.stderr)
        return 2
    print(ask(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
