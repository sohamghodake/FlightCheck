import openai as oa

client = oa.OpenAI(base_url="https://bharatcode.ai/api/model/v1", timeout=30, max_retries=2)


def ask(question):
    return client.responses.create(model="qwen-3.8-27b", input=question, store=False)
