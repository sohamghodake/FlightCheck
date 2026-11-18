from groq import AsyncGroq as AG
from groq import Groq

client = Groq()


def ask(question):
    return client.chat.completions.create(
        model="openai/gpt-oss-20b",
        messages=[{"role": "user", "content": question}],
    )


async def ask_async(question):
    aclient = AG(timeout=30)
    return await aclient.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[{"role": "user", "content": question}],
    )
