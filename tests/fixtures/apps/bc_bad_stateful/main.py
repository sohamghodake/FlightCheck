"""Relies on server-side conversation state, which BharatCode does not keep.

Usage: python main.py "your question"
"""

# INTENTIONALLY FLAWED FIXTURE: store=True and previous_response_id on BharatCode.
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


def ask(question: str, saved_response_id: str = "resp_saved_conversation") -> str:
    try:
        first = client.responses.create(
            model=MODEL, input=question, previous_response_id=saved_response_id, store=True
        )
        follow_up = client.responses.create(
            model=MODEL,
            input="Now answer again in one sentence.",
            previous_response_id=first.id,
            store=True,
        )
    except openai.APIStatusError as exc:
        raise SystemExit(f"BharatCode error {exc.status_code} (request {exc.request_id})") from exc
    except openai.APIConnectionError as exc:
        raise SystemExit(f"Could not reach BharatCode: {exc}") from exc
    if follow_up.status == "incomplete":
        raise SystemExit("Answer truncated")
    return follow_up.output_text


def main() -> int:
    if len(sys.argv) < 2:
        print('usage: python main.py "question"', file=sys.stderr)
        return 2
    print(ask(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
