"""LLM ผ่าน API แบบ OpenAI-compatible (OpenRouter / Typhoon / vLLM / Ollama ใช้โค้ดเดียวกัน)"""
import time

from . import config

_clients = {}


def client(base_url: str | None = None, api_key: str | None = None):
    from openai import OpenAI
    base_url = base_url or config.LLM_BASE_URL
    key = (base_url, api_key or config.LLM_API_KEY)
    if key not in _clients:
        if not key[1]:
            raise RuntimeError("ยังไม่ได้ตั้ง LLM_API_KEY (หรือ OPENROUTER_API_KEY) ใน .env")
        _clients[key] = OpenAI(base_url=base_url, api_key=key[1], timeout=config.LLM_TIMEOUT_S)
    return _clients[key]


def chat(messages, tools=None, model=None, temperature=None, base_url=None, retries=2):
    kw = dict(model=model or config.LLM_MODEL, messages=messages,
              temperature=config.TEMPERATURE if temperature is None else temperature)
    if tools:
        kw.update(tools=tools, tool_choice="auto")
    err = None
    for i in range(retries + 1):
        try:
            return client(base_url).chat.completions.create(**kw)
        except Exception as e:          # network / rate limit
            err = e
            time.sleep(2 * (i + 1))
    raise err


def chat_plain(prompt: str, model=None, base_url=None, temperature=0.0) -> str:
    r = chat([{"role": "user", "content": prompt}], model=model, base_url=base_url, temperature=temperature)
    return r.choices[0].message.content or ""
