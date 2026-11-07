"""Streams an answer but only ever looks at text deltas.

Usage: python main.py "your question"
"""

# INTENTIONALLY FLAWED FIXTURE: terminal stream events are never handled.
import os
import sys

import openai
from openai import OpenAI

MODEL = "qwen-3.8-27b"

client = OpenAI(
    base_url=os.environ["BHARATCODE_BASE_URL"],
    api_key=os.environ["BHARATCODE_API_KEY"],
    timeout=300.0,
)


def ask(question: str) -> str:
    parts: list[str] = []
    try:
        stream = client.responses.create(model=MODEL, input=question, store=False, stream=True)
        for event in stream:
            if event.type == "response.output_text.delta":
                parts.append(event.delta)
    except openai.APIStatusError as exc:
        raise SystemExit(f"BharatCode error {exc.status_code} (request {exc.request_id})") from exc
    except openai.APIConnectionError as exc:
        raise SystemExit(f"Could not reach BharatCode: {exc}") from exc
    return "".join(parts)


def main() -> int:
    if len(sys.argv) < 2:
        print('usage: python main.py "question"', file=sys.stderr)
        return 2
    print(ask(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
