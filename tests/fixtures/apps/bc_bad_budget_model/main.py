"""Asks for an undocumented model with an output budget above the 32 000 cap.

Usage: python main.py "your question"
"""

# INTENTIONALLY FLAWED FIXTURE: model gpt-4o and max_tokens=64000 on BharatCode.
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
    try:
        completion = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": question}],
            max_tokens=64000,
        )
    except openai.APIStatusError as exc:
        raise SystemExit(f"BharatCode error {exc.status_code} (request {exc.request_id})") from exc
    except openai.APIConnectionError as exc:
        raise SystemExit(f"Could not reach BharatCode: {exc}") from exc
    choice = completion.choices[0]
    if choice.finish_reason == "length":
        raise SystemExit("Answer truncated")
    return choice.message.content or ""


def main() -> int:
    if len(sys.argv) < 2:
        print('usage: python main.py "question"', file=sys.stderr)
        return 2
    print(ask(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
