"""Real vision test. Usage: python scripts/test_vision.py path/to/image.jpg"""
import asyncio
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, HERE.parent / "backend")

import llm  # noqa: E402
from scene import SCENE_PROMPT  # same prompt production uses  # noqa: E402


async def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python scripts/test_vision.py path/to/image.jpg")
        sys.exit(2)
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"image not found: {path}")
        sys.exit(2)
    image = path.read_bytes()
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    print(f"vision model: {llm.get('GROQ_VISION_MODEL', 'qwen/qwen3.8-27b')}, image: {len(image)} bytes")
    t0 = time.perf_counter()
    try:
        data = await llm.vision(image, SCENE_PROMPT, mime=mime)
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED: {exc}")
        print("  -> set GROQ_API_KEY and a vision model in GROQ_VISION_MODEL in backend/.env")
        sys.exit(1)
    ms = (time.perf_counter() - t0) * 1000
    objs = data.get("objects", data if isinstance(data, list) else [])
    print(f"latency: {ms:.0f} ms, objects: {len(objs)}")
    for o in objs[:12]:
        bbox = o.get("bbox")
        print(f"  {o.get('id')}: {o.get('label')} bbox={bbox} hint={(o.get('hint') or '')[:60]}")


if __name__ == "__main__":
    asyncio.run(main())
