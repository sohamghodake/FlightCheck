"""FC-BC-001 good: store=True on a non-BharatCode client is out of scope for BC rules."""

from openai import OpenAI

other = OpenAI(base_url="https://api.example.com/v1", api_key="not-a-real-key")


def ask(messages):
    return other.responses.create(model="some-model", input=messages, store=True)
