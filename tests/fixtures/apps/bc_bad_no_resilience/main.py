"""No error handling, no retries and no explicit timeout.

Usage: python main.py "your question"
"""

# INTENTIONALLY FLAWED FIXTURE: no try/except, max_retries=0 and the 10-minute SDK default timeout.
import os
import sys

from openai import OpenAI

MODEL = "qwen-3.8-27b"

client = OpenAI(
    base_url=os.environ["BHARATCODE_BASE_URL"],
    api_key=os.environ["BHARATCODE_API_KEY"],
    max_retries=0,
)


def ask(question: str) -> str:
    response = client.responses.create(model=MODEL, input=question, store=False)
    if response.status == "incomplete":
        raise SystemExit("Answer truncated")
    return response.output_text


def main() -> int:
    if len(sys.argv) < 2:
        print('usage: python main.py "question"', file=sys.stderr)
        return 2
    print(ask(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
