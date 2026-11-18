"""Relies on server-side state that Groq's Responses API does not keep.

Usage: python main.py "your question"
"""

# INTENTIONALLY FLAWED FIXTURE: store=True and previous_response_id on Groq's /responses.
import os
import sys

import openai
from openai import OpenAI

MODEL = "openai/gpt-oss-20b"

client = OpenAI(
    base_url=os.environ.get("GROQ_OPENAI_BASE_URL", "https://api.groq.com/openai/v1"),
    api_key=os.environ["GROQ_API_KEY"],
    timeout=60.0,
)


def ask(question: str) -> str:
    try:
        first = client.responses.create(model=MODEL, input=question, store=True)
        follow_up = client.responses.create(
            model=MODEL,
            input="Answer in one sentence.",
            previous_response_id=first.id,
        )
    except openai.APIStatusError as exc:
        raise SystemExit(f"Groq error {exc.status_code}: {exc.message}") from exc
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
