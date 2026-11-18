"""Streams a Groq Chat answer and prints whatever arrived.

Usage: python main.py "your question"
"""

# INTENTIONALLY FLAWED FIXTURE: ignores finish_reason and x_groq.error, so a stream Groq stopped
# early (or a truncated answer) is printed as if it were complete.
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
    parts: list[str] = []
    try:
        stream = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": question}],
            stream=True,
            max_completion_tokens=2000,
        )
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                parts.append(chunk.choices[0].delta.content)
    except openai.APIStatusError as exc:
        raise SystemExit(f"Groq error {exc.status_code}: {exc.message}") from exc
    return "".join(parts)


def main() -> int:
    if len(sys.argv) < 2:
        print('usage: python main.py "question"', file=sys.stderr)
        return 2
    print(ask(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
