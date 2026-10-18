"""List the models this Groq key can actually use. Usage: python scripts/list_models.py"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from llm import _get_client, BASE_URL, _check_key  # noqa: E402


async def main() -> None:
    key = _check_key()
    resp = await _get_client().get(
        f"{BASE_URL}/models", headers={"Authorization": f"Bearer {key}"}
    )
    data = resp.json()
    ids = sorted(m["id"] for m in data.get("data", []))
    print("\n".join(ids))


asyncio.run(main())
