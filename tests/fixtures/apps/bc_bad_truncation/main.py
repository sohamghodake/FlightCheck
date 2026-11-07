"""Uses output_text without checking whether the answer was cut short.

Usage: python main.py "your question"
"""

# INTENTIONALLY FLAWED FIXTURE: status/incomplete_details are never checked and the budget is tiny.
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
        response = client.responses.create(
            model=MODEL, input=question, store=False, max_output_tokens=16
        )
    except openai.APIStatusError as exc:
        raise SystemExit(f"BharatCode error {exc.status_code} (request {exc.request_id})") from exc
    except openai.APIConnectionError as exc:
        raise SystemExit(f"Could not reach BharatCode: {exc}") from exc
    return response.output_text


def main() -> int:
    if len(sys.argv) < 2:
        print('usage: python main.py "question"', file=sys.stderr)
        return 2
    print(ask(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
