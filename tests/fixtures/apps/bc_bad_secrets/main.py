"""Hard-codes the API key and prints it.

Usage: python main.py "your question"
"""

# INTENTIONALLY FLAWED FIXTURE: a (fake) hard-coded key that is also printed.
import os
import sys

import openai
from openai import OpenAI

MODEL = "qwen-3.8-27b"

API_KEY = "bc_live_FAKE0000000000000000"

client = OpenAI(
    base_url=os.environ["BHARATCODE_BASE_URL"],
    api_key=API_KEY,
    timeout=300.0,
)


def ask(question: str) -> str:
    print(f"Calling BharatCode with key {API_KEY}")
    try:
        response = client.responses.create(model=MODEL, input=question, store=False)
    except openai.APIStatusError as exc:
        raise SystemExit(f"BharatCode error {exc.status_code} (request {exc.request_id})") from exc
    except openai.APIConnectionError as exc:
        raise SystemExit(f"Could not reach BharatCode: {exc}") from exc
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
