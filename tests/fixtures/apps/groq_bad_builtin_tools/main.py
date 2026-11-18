"""Asks a model without built-in tools to use Groq's browser_search.

Usage: python main.py "your question"
"""

# INTENTIONALLY FLAWED FIXTURE: browser_search on a model that is not a GPT-OSS model.
import os
import sys

import openai
from openai import OpenAI

MODEL = "qwen/qwen3.8-27b"

client = OpenAI(
    base_url=os.environ.get("GROQ_OPENAI_BASE_URL", "https://api.groq.com/openai/v1"),
    api_key=os.environ["GROQ_API_KEY"],
    timeout=60.0,
)


def ask(question: str) -> str:
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": question}],
            tools=[{"type": "browser_search"}],
            max_completion_tokens=2000,
        )
    except openai.APIStatusError as exc:
        raise SystemExit(f"Groq error {exc.status_code}: {exc.message}") from exc
    choice = response.choices[0]
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
