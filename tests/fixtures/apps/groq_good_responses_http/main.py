"""Groq Responses API with a function tool over raw HTTP, done right (clean fixture).

Lays out URLs the way the groq SDK does: the base URL is the host root (GROQ_BASE_URL) and every
path carries /openai/v1. The conversation is replayed in input on every request (the Responses
API is not stateful), status "incomplete" is surfaced, tool results are JSON strings, and 429 /
5xx answers are retried a bounded number of times, waiting for retry-after when Groq sends it
and backing off exponentially otherwise.

Usage: python main.py "your question"
"""

import json
import os
import random
import sys
import time

import httpx2

MODEL = "openai/gpt-oss-20b"
MAX_ATTEMPTS = 4

TOOLS = [
    {
        "type": "function",
        "name": "get_weather",
        "description": "Current weather for a city.",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    }
]

client = httpx2.Client(
    base_url=os.environ.get("GROQ_BASE_URL", "https://api.groq.com"),
    headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"},
    timeout=60.0,
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


def create_response(payload: dict) -> dict:
    """POST /openai/v1/responses with bounded retries for 429 and 5xx."""
    for attempt in range(MAX_ATTEMPTS):
        try:
            response = client.post("/openai/v1/responses", json=payload)
        except httpx2.HTTPError as exc:
            raise SystemExit(f"Could not reach Groq: {exc}") from exc
        if response.status_code == 429 or response.status_code >= 500:
            if attempt == MAX_ATTEMPTS - 1:
                break
            retry_after = response.headers.get("retry-after")
            delay = float(retry_after) if retry_after else 0.5 * 2**attempt
            time.sleep(delay + random.uniform(0, 0.1))
            continue
        if response.status_code >= 400:
            message = response.json().get("error", {}).get("message", "")
            raise SystemExit(f"Groq error {response.status_code}: {message}")
        return response.json()
    raise SystemExit(f"Groq still failing after {MAX_ATTEMPTS} attempts")


def output_text(body: dict) -> str:
    return "".join(
        part["text"]
        for item in body["output"]
        if item["type"] == "message"
        for part in item["content"]
        if part["type"] == "output_text"
    )


def checked(body: dict) -> dict:
    if body["status"] == "incomplete":
        reason = (body.get("incomplete_details") or {}).get("reason")
        raise SystemExit(f"Answer truncated ({reason}); partial output: {output_text(body)!r}")
    return body


def ask(question: str) -> str:
    items: list = [{"role": "user", "content": question}]
    request = {"model": MODEL, "tools": TOOLS, "max_output_tokens": 2000, "store": False}
    body = checked(create_response({**request, "input": items}))
    calls = [item for item in body["output"] if item["type"] == "function_call"]
    if calls:
        items.extend(body["output"])
        for call in calls:
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": call["call_id"],
                    "output": json.dumps(run_tool(call)),
                }
            )
        body = checked(create_response({**request, "input": items}))
    return output_text(body)


def main() -> int:
    if len(sys.argv) < 2:
        print('usage: python main.py "question"', file=sys.stderr)
        return 2
    print(ask(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
