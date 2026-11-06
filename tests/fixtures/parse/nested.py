from openai import AsyncOpenAI, OpenAI

client = OpenAI()


def outer():
    local = AsyncOpenAI(base_url="https://llm.example.com/v1")

    async def inner(prompt):
        return await local.responses.create(model="qwen-3.8-27b", input=prompt)

    return inner


def lambda_receiver(prompt):
    return (lambda: client)().responses.create(model="qwen-3.8-27b", input=prompt)


def walrus(prompt):
    if c := OpenAI():
        return c.responses.create(model="qwen-3.8-27b", input=prompt)
    return None


def unknown_param(client_arg, prompt):
    return client_arg.responses.create(model="qwen-3.8-27b", input=prompt)
