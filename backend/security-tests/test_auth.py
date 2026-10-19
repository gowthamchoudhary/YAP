import asyncio
import datetime
import json
import os
from pathlib import Path

import httpx

API_URL = "https://api.assemblyai.com/v2/account"
ENV_PATH = Path(__file__).with_name(".env")
OUTPUT_PATH = Path(__file__).with_name("findings_auth.json")


def record_result(results, name, *, response=None, error=None):
    entry = {
        "test": name,
        "time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    if response is not None:
        entry["status"] = response.status_code
        # Keep output safe: response bodies may include account details.
    if error is not None:
        entry["error"] = error
    results.append(entry)
    print(name, "->", entry.get("status", entry.get("error", "unknown error")))


async def main():
    key = os.environ.get("AAI_TEST_KEY")
    if not key and ENV_PATH.is_file():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                name, value = line.split("=", 1)
                if name.strip() == "AAI_TEST_KEY":
                    key = value.strip().strip("\"'")
                    break
    if not key:
        raise SystemExit(f"Set AAI_TEST_KEY in {ENV_PATH} or in the environment before running this script.")

    results = []
    cases = [
        ("valid_key", {"Authorization": key}),
        ("no_key", None),
        ("empty_key", {"Authorization": ""}),
        ("bearer_prefix", {"Authorization": f"Bearer {key}"}),
        ("truncated_key", {"Authorization": key[:-4]}),
        ("whitespace_key", {"Authorization": key + " "}),
        ("garbage_key", {"Authorization": "0" * len(key)}),
    ]

    timeout = httpx.Timeout(15.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for name, headers in cases:
            try:
                response = await client.get(API_URL, headers=headers)
                record_result(results, name, response=response)
            except (httpx.HTTPError, ValueError) as exc:
                # ValueError can be raised for invalid header values; continue with other cases.
                record_result(results, name, error=type(exc).__name__)

    OUTPUT_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"Wrote status-only results to {OUTPUT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())

