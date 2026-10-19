"""Provider-agnostic LLM wrapper for Groq and AssemblyAI LLM Gateway.

Both functions:
  - are async (driven by engine.py's asyncio tasks),
  - log timing + errors through apilog,
  - raise config.MissingEnvError naming the missing env var when a key is absent.
"""
import asyncio
import base64
from contextvars import ContextVar
import json
import re
import sys
import time
from typing import Any

import httpx

import apilog
from config import get, get_int, require

GROQ_BASE_URL = (get("GROQ_BASE_URL", "https://api.groq.com/openai/v1") or "").rstrip("/")
GATEWAY_BASE_URL = "https://llm-gateway.assemblyai.com/v1"
GATEWAY_MODEL = "qwen3.5-4b-32k-fast"
_last_chat_backend: ContextVar[str | None] = ContextVar("last_chat_backend", default=None)


def get_text_backend() -> str:
    backend = (get("LLM_BACKEND", "groq") or "groq").strip().lower()
    if backend not in {"groq", "gateway"}:
        raise ValueError("LLM_BACKEND must be 'groq' or 'gateway'.")
    return backend


def last_chat_backend() -> str | None:
    """Return the provider that served the most recent chat call in this task."""
    return _last_chat_backend.get()


def require_text_backend_key(backend: str | None = None) -> str:
    backend = backend or get_text_backend()
    if backend == "gateway":
        key = get("ASSEMBLYAI_API_KEY") or get("AAI_TEST_KEY")
        if key and key.strip():
            return key.strip()
        return require("ASSEMBLYAI_API_KEY", "Needed for AssemblyAI LLM Gateway text generation.")
    if backend != "groq":
        raise ValueError("LLM_BACKEND must be 'groq' or 'gateway'.")
    return require(
        "GROQ_API_KEY", "Get one at https://console.groq.com/keys and put it in backend/.env"
    )

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
    """Groq key for vision requests, which remain on Groq."""
    return require_text_backend_key("groq")


_RETRY_IN_RE = re.compile(r"try again in ([\d.]+)\s*(ms|s)", re.IGNORECASE)


def _suggested_backoff_s(err_text: str) -> float:
    m = _RETRY_IN_RE.search(err_text or "")
    if not m:
        return 2.0
    delay = float(m.group(1))
    return min(delay / 1000.0 if m.group(2).lower() == "ms" else delay, 15.0)


async def _post(
    payload: dict[str, Any], service: str, backend: str = "groq"
) -> dict[str, Any]:
    """POST to chat completions with timing + apilog. Raises on any error.

    On a 429 the server-suggested 'try again in <delay>' is honoured with ONE
    patient retry so the provider is never hammered.
    """
    key = require_text_backend_key(backend)
    base_url = GROQ_BASE_URL if backend == "groq" else GATEWAY_BASE_URL
    auth_value = f"Bearer {key}" if backend == "groq" else key
    provider = "Groq" if backend == "groq" else "AssemblyAI LLM Gateway"
    headers = {"Authorization": auth_value, "Content-Type": "application/json"}
    body = json.dumps(payload).encode("utf-8")
    t0 = time.perf_counter()
    status, err, status_code = "ok", None, None
    try:
        for attempt in (1, 2):
            resp = await _get_client().post(
                f"{base_url}/chat/completions", headers=headers, content=body
            )
            status_code = resp.status_code
            if resp.status_code == 429 and attempt == 1:
                delay = _suggested_backoff_s(resp.text)
                print(
                    f"[llm] 429 rate limited; waiting {delay:.2f}s before the one retry",
                    file=sys.stderr,
                )
                await asyncio.sleep(delay)
                continue
            break
        if resp.status_code == 401:
            raise RuntimeError(
                f"{provider} rejected the API key (401). Check the configured key in backend/.env."
            )
        if resp.status_code != 200:
            raise RuntimeError(
                f"{provider} chat completions returned {resp.status_code}: {resp.text[:400]}"
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
            endpoint=f"{base_url}/chat/completions",
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
    backend: str | None = None,
) -> str:
    """One chat completion through the selected text backend.

    `messages` entries may be full {"role", "content"} dicts or bare prompt
    strings; strings are normalized to user messages before sending.
    """
    backend = backend or get_text_backend()
    if backend not in {"groq", "gateway"}:
        raise ValueError("backend must be 'groq' or 'gateway'.")
    if model is None:
        model = (
            get("GROQ_TEXT_MODEL", "openai/gpt-oss-120b")
            if backend == "groq"
            else get("GATEWAY_TEXT_MODEL", GATEWAY_MODEL)
        )
    normalized = [
        m if isinstance(m, dict) else {"role": "user", "content": str(m)} for m in messages
    ]
    payload: dict[str, Any] = {
        "model": model,
        "messages": normalized,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if response_format:
        payload["response_format"] = response_format
    # gpt-oss (reasoning-channel models) reliably fail Groq's strict json_object
    # mode for our prompts with 400 json_validate_failed; the prompt already
    # demands JSON and _parse_json is lenient, so skip the response_format for
    # those models instead of paying a 400 + full second call per line.
    if response_format and "gpt-oss" in model:
        payload.pop("response_format", None)
    if backend == "groq" and "gpt-oss" in model:
        # gpt-oss reasoning tokens count against max_tokens, so with the default
        # 'medium' effort the visible JSON/line truncates mid-string. 'low' is the
        # documented Groq knob for conversational gpt-oss workloads.
        payload["reasoning_effort"] = get("GROQ_REASONING_EFFORT", "low")

    async def complete(request_payload: dict[str, Any], provider: str, provider_model: str) -> str:
        try:
            data = await _post(
                request_payload,
                service=f"{provider}.text[{provider_model}]",
                backend=provider,
            )
        except RuntimeError as exc:
            # Groq gpt-oss occasionally rejects JSON mode with no sampled reply.
            # Retry Groq once without JSON mode before using the cross-provider fallback.
            if provider != "groq" or "json_validate_failed" not in str(exc):
                raise
            print(
                "[llm] json_validate_failed (no reply sampled); retrying without json mode",
                file=sys.stderr,
            )
            request_payload = dict(request_payload)
            request_payload.pop("response_format", None)
            data = await _post(
                request_payload,
                service=f"{provider}.text[{provider_model}] (no-json retry)",
                backend=provider,
            )
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"{provider} returned an unexpected response shape: {data}") from exc
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError(f"{provider} returned an empty chat completion.")
        return content

    _last_chat_backend.set(None)
    try:
        content = await complete(payload, backend, model)
        _last_chat_backend.set(backend)
        return content
    except Exception as groq_error:
        if backend != "groq":
            raise
        gateway_model = get("GATEWAY_TEXT_MODEL", GATEWAY_MODEL)
        gateway_payload = dict(payload)
        gateway_payload["model"] = gateway_model
        gateway_payload.pop("reasoning_effort", None)
        print(
            f"[llm] Groq text call failed ({type(groq_error).__name__}); "
            f"retrying once on gateway.text[{gateway_model}]",
            file=sys.stderr,
        )
        try:
            content = await complete(gateway_payload, "gateway", gateway_model)
        except Exception as gateway_error:
            raise RuntimeError(
                "Groq text call failed and Gateway fallback also failed: "
                f"{type(gateway_error).__name__}: {gateway_error}"
            ) from gateway_error
        _last_chat_backend.set("gateway")
        return content


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
        # 900: the 12-object scene JSON needs only a few hundred tokens, and Groq's
        # on-demand tier enforces a 1000 output-token-per-minute budget for this model
        "max_tokens": get_int("GROQ_VISION_MAX_TOKENS", 900),
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    data = await _post(payload, service=f"groq.vision[{model}]", backend="groq")
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
