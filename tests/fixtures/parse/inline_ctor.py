from openai import OpenAI


def one_shot(prompt):
    return OpenAI().chat.completions.create(model="gpt-x", messages=[prompt], stream=True)
