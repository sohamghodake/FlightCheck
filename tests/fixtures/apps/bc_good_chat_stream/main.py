"""Chat Completions streaming done right for BharatCode (clean fixture).

finish_reason is checked, streamed tool calls are executed only when complete, and the
assistant tool_calls message is followed by matching tool messages.

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
        "function": {
            "name": "get_weather",
            "description": "Current weather for a city.",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    }
]

client = OpenAI(
    base_url=os.environ["BHARATCODE_BASE_URL"],
    api_key=os.environ["BHARATCODE_API_KEY"],
    timeout=300.0,
)


def get_weather(city: str) -> dict:
    return {"city": city, "temp_c": 31, "sky": "clear"}


def run_tool(call: dict) -> dict:
    try:
        args = json.loads(call["arguments"] or "{}")
    except json.JSONDecodeError as exc:
        return {"error": f"invalid arguments: {exc}"}
    if call["name"] != "get_weather":
        return {"error": f"unknown tool {call['name']}"}
    return get_weather(str(args.get("city", "")))


def stream_turn(messages: list) -> tuple[str, list[dict], str | None]:
    """Stream one assistant turn: (text, tool calls, finish_reason)."""
    parts: list[str] = []
    calls: dict[int, dict] = {}
    finish_reason = None
    try:
        stream = client.chat.completions.create(
            model=MODEL, messages=messages, tools=TOOLS, stream=True, max_tokens=2000
        )
        for chunk in stream:
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            if choice.delta.content:
                parts.append(choice.delta.content)
            for piece in choice.delta.tool_calls or []:
                slot = calls.setdefault(piece.index, {"id": "", "name": "", "arguments": ""})
                if piece.id:
                    slot["id"] = piece.id
                if piece.function and piece.function.name:
                    slot["name"] = piece.function.name
                if piece.function and piece.function.arguments:
                    slot["arguments"] += piece.function.arguments
            if choice.finish_reason:
                finish_reason = choice.finish_reason
    except openai.APIStatusError as exc:
        raise SystemExit(f"BharatCode error {exc.status_code} (request {exc.request_id})") from exc
    except openai.APIConnectionError as exc:
        raise SystemExit(f"Could not reach BharatCode: {exc}") from exc
    if finish_reason is None:
        raise SystemExit("Stream ended without a finish_reason")
    return "".join(parts), [calls[i] for i in sorted(calls)], finish_reason


def ask(question: str) -> str:
    messages: list = [{"role": "user", "content": question}]
    text, calls, finish_reason = stream_turn(messages)
    if finish_reason == "tool_calls" and calls:
        tool_calls = [
            {
                "id": call["id"],
                "type": "function",
                "function": {"name": call["name"], "arguments": call["arguments"]},
            }
            for call in calls
        ]
        messages.append({"role": "assistant", "content": text or None, "tool_calls": tool_calls})
        for call in calls:
            messages.append(
                {"role": "tool", "tool_call_id": call["id"], "content": json.dumps(run_tool(call))}
            )
        text, _, finish_reason = stream_turn(messages)
    if finish_reason == "length":
        raise SystemExit(f"Answer truncated; partial output: {text!r}")
    return text


def main() -> int:
    if len(sys.argv) < 2:
        print('usage: python main.py "question"', file=sys.stderr)
        return 2
    print(ask(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
