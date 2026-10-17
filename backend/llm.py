"""Provider-agnostic LLM wrapper.

Everything that talks to a *text/vision* model goes through here so the
provider can be swapped by editing this one file. Current provider: Groq
(OpenAI-compatible chat completions at https://api.groq.com/openai/v1).

Both functions:
  - are async (driven by engine.py's asyncio tasks),
  - log timing + errors through apilog,
  - raise config.MissingEnvError naming the missing env var when a key is absent.
"""
import base64
import json
import sys
import time
from typing import Any

import httpx

import apilog
from config import get, require

PROVIDER = "groq"
BASE_URL = (get("GROQ_BASE_URL", "https://api.groq.com/openai/v1") or "").rstrip("/")

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Keep-alive client shared by all calls."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(45.0), limits=httpx.Limits(max_connections=8)
        )
    return _client


def _check_key() -> str:
    return require(
        "GROQ_API_KEY", "Get one at https://console.groq.com/keys and put it in backend/.env"
    )


async def _post(payload: dict[str, Any], service: str) -> dict[str, Any]:
    """POST to chat completions with timing + apilog. Raises on any error."""
    key = _check_key()
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    body = json.dumps(payload).encode("utf-8")
    t0 = time.perf_counter()
    status, err, status_code = "ok", None, None
    try:
        resp = await _get_client().post(
            f"{BASE_URL}/chat/completions", headers=headers, content=body
        )
        status_code = resp.status_code
        if resp.status_code == 401:
            raise RuntimeError(
                "Groq rejected the API key (401). Check GROQ_API_KEY in backend/.env."
            )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Groq chat completions returned {resp.status_code}: {resp.text[:400]}"
            )
        return resp.json()
    except Exception as exc:  # noqa: BLE001 - re-raised after logging
        err = f"{type(exc).__name__}: {exc}"
        status = "error"
        raise
    finally:
        latency = (time.perf_counter() - t0) * 1000
        apilog.log_call(
            service=service,
            endpoint=f"{BASE_URL}/chat/completions",
            status=status,
            latency_ms=latency,
            error=err,
            request_bytes=len(body),
            response_bytes=None,
            status_code=status_code,
        )
        print(
            f"[llm] {service} latency={latency:.0f}ms status={status}"
            f"{' err=' + err if err else ''}",
            file=sys.stderr,
        )


async def chat(
    messages: list[dict[str, Any]],
    max_tokens: int = 300,
    temperature: float = 0.8,
    model: str | None = None,
    response_format: dict[str, Any] | None = None,
) -> str:
    """One chat completion. Returns assistant text (documented current provider: Groq)."""
    model = model or get("GROQ_TEXT_MODEL", "llama-3.3-70b-versatile")
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if response_format:
        payload["response_format"] = response_format
    data = await _post(payload, service=f"groq.text[{model}]")
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Groq returned an unexpected shape: {data}") from exc


async def vision(image_bytes: bytes, prompt: str, mime: str = "image/jpeg") -> dict[str, Any]:
    """Vision call: image bytes + prompt -> JSON object (json_object mode is forced)."""
    _check_key()
    model = get("GROQ_VISION_MODEL", "qwen/qwen3.8-27b")
    data_url = f"data:{mime};base64,{base64.b64encode(image_bytes).decode('ascii')}"
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"{prompt}\n(Always answer with a single valid JSON object)",
                    },
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        "max_tokens": 1200,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    data = await _post(payload, service=f"groq.vision[{model}]")
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Groq returned an unexpected shape: {data}") from exc
    return _parse_json(text)


def _parse_json(text: str) -> dict[str, Any]:
    """Model JSON with code fences / whitespace tolerated."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    return json.loads(cleaned)


if __name__ == "__main__":
    import asyncio

    async def _main() -> None:
        print(await chat([{"role": "user", "content": "Say exactly: LLM OK"}], max_tokens=10))

    asyncio.run(_main())
