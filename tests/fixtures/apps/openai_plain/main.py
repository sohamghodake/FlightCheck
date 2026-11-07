"""Plain OpenAI usage with no base URL: the provider negative control (clean fixture).

The SDK reads OPENAI_API_KEY (and OPENAI_BASE_URL, if set) from the environment.

Usage: python main.py "your question"
"""

import sys

import openai
from openai import OpenAI

client = OpenAI(timeout=60.0)


def ask(question: str) -> str:
    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": question}],
            max_tokens=500,
        )
    except openai.APIError as exc:
        raise SystemExit(f"OpenAI error: {exc}") from exc
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
