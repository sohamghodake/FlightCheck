"""Responses tool loop with a hard-coded call_id and a non-string tool output.

Usage: python main.py "your question"
"""

# INTENTIONALLY FLAWED FIXTURE: call_id is hard-coded to "call_1" and the tool output is a dict.
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
        raise SystemExit("Answer truncated")
    return response


def ask(question: str) -> str:
    history: list = [{"role": "user", "content": question}]
    response = create(history)
    calls = [item for item in response.output if item.type == "function_call"]
    if calls:
        history.extend(response.output)
        try:
            args = json.loads(calls[0].arguments)
        except json.JSONDecodeError:
            args = {}
        history.append(
            {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": {"city": args.get("city"), "temp_c": 31},
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
