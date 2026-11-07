"""Checks finish_reason in a helper function: a known FC-GEN-001 false positive.

The check is correct, but FC-GEN-001 is lexical and only looks inside the function that
makes the call, so this app is expected to produce that false positive (plan §16.2).

Usage: python main.py "your question"
"""

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


def check_complete(choice) -> None:
    if choice.finish_reason == "length":
        raise SystemExit("Answer truncated")


def ask(question: str) -> str:
    try:
        completion = client.chat.completions.create(
            model=MODEL, messages=[{"role": "user", "content": question}]
        )
    except openai.APIStatusError as exc:
        raise SystemExit(f"BharatCode error {exc.status_code} (request {exc.request_id})") from exc
    except openai.APIConnectionError as exc:
        raise SystemExit(f"Could not reach BharatCode: {exc}") from exc
    choice = completion.choices[0]
    check_complete(choice)
    return choice.message.content or ""


def main() -> int:
    if len(sys.argv) < 2:
        print('usage: python main.py "question"', file=sys.stderr)
        return 2
    print(ask(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
