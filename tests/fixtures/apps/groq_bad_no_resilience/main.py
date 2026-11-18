"""Retries failed Groq requests in a tight loop.

Usage: python main.py "your question"
"""

# INTENTIONALLY FLAWED FIXTURE: SDK retries off, then an own retry loop that neither backs off
# nor waits for retry-after after a 429.
import os
import sys

import openai
from openai import OpenAI

MODEL = "openai/gpt-oss-20b"
ATTEMPTS = 3

client = OpenAI(
    base_url=os.environ.get("GROQ_OPENAI_BASE_URL", "https://api.groq.com/openai/v1"),
    api_key=os.environ["GROQ_API_KEY"],
    timeout=60.0,
    max_retries=0,
)


def ask(question: str) -> str:
    last: Exception | None = None
    for _ in range(ATTEMPTS):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "user", "content": question}],
                max_completion_tokens=2000,
            )
        except (openai.RateLimitError, openai.InternalServerError) as exc:
            last = exc
            continue
        choice = response.choices[0]
        if choice.finish_reason == "length":
            raise SystemExit("Answer truncated")
        return choice.message.content or ""
    raise SystemExit(f"Groq kept failing: {last}")


def main() -> int:
    if len(sys.argv) < 2:
        print('usage: python main.py "question"', file=sys.stderr)
        return 2
    print(ask(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
