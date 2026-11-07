"""Asks BharatCode to run a hosted tool in a background job.

Usage: python main.py "your question"
"""

# INTENTIONALLY FLAWED FIXTURE: hosted web_search tool and background=True on BharatCode.
import os
import sys
import time

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
            model=MODEL,
            input=question,
            tools=[{"type": "web_search"}],
            background=True,
            store=False,
        )
        while response.status in ("queued", "in_progress"):
            time.sleep(2)
            response = client.responses.retrieve(response.id)
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
