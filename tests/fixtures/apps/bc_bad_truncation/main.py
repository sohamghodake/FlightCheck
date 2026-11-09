"""Uses output_text without checking whether the answer was cut short.

Usage: python main.py "your question"
"""

# INTENTIONALLY FLAWED FIXTURE: status/incomplete_details are never checked and the budget is
# tiny. A function call is run even when the response was truncated (its arguments may be cut
# off), and an answer that "looks unfinished" is fetched again with the identical request.
import json
import os
import sys

import openai
from openai import OpenAI

MODEL = "qwen-3.8-27b"
MAX_ROUNDS = 4

TOOLS = [
    {
        "type": "function",
        "name": "get_weather",
        "description": "Current weather for a city.",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
            "additionalProperties": False,
        },
        "strict": True,
    }
]

client = OpenAI(
    base_url=os.environ["BHARATCODE_BASE_URL"],
    api_key=os.environ["BHARATCODE_API_KEY"],
    timeout=300.0,
)


def run_tool(item) -> dict:
    try:
        args = json.loads(item.arguments)
    except json.JSONDecodeError:
        args = {}  # cut-off arguments: carry on with whatever we have
    return {"city": str(args.get("city", "")), "temp_c": 31, "sky": "clear"}


def ask(question: str) -> str:
    history: list = [{"role": "user", "content": question}]
    retried = False
    answer = ""
    for _ in range(MAX_ROUNDS):
        try:
            response = client.responses.create(
                model=MODEL, input=history, tools=TOOLS, store=False, max_output_tokens=16
            )
        except openai.APIStatusError as exc:
            raise SystemExit(
                f"BharatCode error {exc.status_code} (request {exc.request_id})"
            ) from exc
        except openai.APIConnectionError as exc:
            raise SystemExit(f"Could not reach BharatCode: {exc}") from exc
        calls = [item for item in response.output if item.type == "function_call"]
        if calls:
            history.extend(response.output)
            for call in calls:
                history.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": json.dumps(run_tool(call)),
                    }
                )
            continue
        answer = response.output_text
        if not retried and not answer.rstrip().endswith((".", "!", "?")):
            retried = True  # looks unfinished: ask again, same request, same budget
            continue
        break
    return answer


def main() -> int:
    if len(sys.argv) < 2:
        print('usage: python main.py "question"', file=sys.stderr)
        return 2
    print(ask(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
