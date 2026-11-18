import httpx2

URL = "https://api.groq.com/openai/v1/chat/completions"


def call(key):
    return httpx2.post(URL, json={"model": "openai/gpt-oss-20b", "messages": []})


session = httpx2.Client(base_url="https://api.groq.com/openai/v1")
