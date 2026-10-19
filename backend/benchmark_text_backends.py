"""Compare Groq text generation with AssemblyAI LLM Gateway on the same prompts."""
import asyncio
import os
import time
from pathlib import Path

import httpx


ENV_PATH = Path(__file__).with_name(".env")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GATEWAY_URL = "https://llm-gateway.assemblyai.com/v1/chat/completions"
GATEWAY_MODEL = "qwen3.5-4b-32k-fast"
SYSTEM_PROMPT = (
    "Write exactly one short spoken line (8-20 words) in the character's voice. "
    "No narration, labels, or quotation marks."
)
PROMPTS = [
    "Mara, a tired night-shift nurse with dry humor, finds a tiny crown in the vending machine. React.",
    "Bram, an overly formal retired knight who fears pigeons, is interrupted by one on his helmet. React.",
    "Pip, an excitable young inventor whose gadgets often misfire, sees smoke coming from a toaster. React.",
    "Sable, a suspicious old lighthouse keeper who speaks in clipped warnings, hears singing below deck. React.",
    "Jun, a gentle, literal-minded gardener who loves bad puns, discovers a tomato shaped like a heart. React.",
    "Vex, a boastful space smuggler secretly afraid of the dark, loses power in the cargo hold. React.",
    "Nora, a precise museum curator who treats objects like people, catches a statue winking. React.",
    "Rook, a cheerful ex-detective who sees clues everywhere, finds a suspiciously clean footprint. React.",
    "Tamsin, a dramatic opera singer with stage fright, is asked to order coffee by name. React.",
    "Ollie, a blunt but loyal mechanic who talks to machines, hears the old engine start by itself. React.",
]


def load_local_env() -> None:
    if not ENV_PATH.is_file():
        return
    for raw_line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(name.strip(), value)


async def generate(client, backend, model, prompt, keys):
    if backend == "groq":
        url = GROQ_URL
        key = keys["groq"]
        authorization = f"Bearer {key}"
    else:
        url = GATEWAY_URL
        key = keys["gateway"]
        authorization = key

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 140,
        "temperature": 0.8,
    }
    if backend == "groq" and "gpt-oss" in model:
        payload["reasoning_effort"] = "low"
    started = time.perf_counter()
    try:
        response = await client.post(
            url,
            headers={"Authorization": authorization, "Content-Type": "application/json"},
            json=payload,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        response.raise_for_status()
        result = response.json()["choices"][0]["message"]["content"]
        return elapsed_ms, result.strip()
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        message = str(exc)
        for secret in keys.values():
            if secret:
                message = message.replace(secret, "[REDACTED]")
        return elapsed_ms, f"ERROR ({type(exc).__name__}): {message[:300]}"


async def main() -> None:
    load_local_env()
    groq_key = os.environ.get("GROQ_API_KEY", "").strip()
    gateway_key = (
        os.environ.get("ASSEMBLYAI_API_KEY") or os.environ.get("AAI_TEST_KEY", "")
    ).strip()
    if not groq_key or not gateway_key:
        raise SystemExit(
            f"Set GROQ_API_KEY and AAI_TEST_KEY (or ASSEMBLYAI_API_KEY) in {ENV_PATH}."
        )

    keys = {"groq": groq_key, "gateway": gateway_key}
    groq_model = os.environ.get("GROQ_TEXT_MODEL", "openai/gpt-oss-120b").strip()
    timeout = httpx.Timeout(45.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        print(f"Groq model: {groq_model} | Gateway model: {GATEWAY_MODEL}")
        for index, prompt in enumerate(PROMPTS, 1):
            groq_result, gateway_result = await asyncio.gather(
                generate(client, "groq", groq_model, prompt, keys),
                generate(client, "gateway", GATEWAY_MODEL, prompt, keys),
            )
            groq_ms, groq_text = groq_result
            gateway_ms, gateway_text = gateway_result
            print(f"\n[{index}] {prompt}")
            print(f"Groq ({groq_ms:.0f} ms): {groq_text}")
            print(f"Gateway ({gateway_ms:.0f} ms): {gateway_text}")


if __name__ == "__main__":
    asyncio.run(main())
