"""Real text-LLM test. Usage: python scripts/test_llm.py [backend_dir]"""
import asyncio
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, HERE.parent / "backend")

import llm  # noqa: E402


async def main() -> None:
    print(f"text model: {llm.get('GROQ_TEXT_MODEL', 'llama-3.3-70b-versatile')}")
    t0 = time.perf_counter()
    try:
        out = await llm.chat(
            [{"role": "user", "content": "Reply with exactly this sentence and nothing else: The lamp is awake."}],
            max_tokens=20,
            temperature=0.1,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED: {exc}")
        print("  -> set GROQ_API_KEY (and optionally GROQ_TEXT_MODEL) in backend/.env")
        sys.exit(1)
    ms = (time.perf_counter() - t0) * 1000
    print(f"response ({ms:.0f} ms): {out.strip()!r}")

    # timed JSON-mode round trip
    t0 = time.perf_counter()
    raw2 = await llm.chat(
        [{"role": "user", "content": "Return JSON: {\"ok\": true}. Only the JSON object, nothing else."}],
        max_tokens=12,
        temperature=0.0,
        response_format={"type": "json_object"},
    )
    ms2 = (time.perf_counter() - t0) * 1000
    print(f"json-mode ({ms2:.0f} ms): {raw2.strip()!r}")


if __name__ == "__main__":
    asyncio.run(main())
