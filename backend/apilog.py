"""Structured, append-only log of every external API call.

One JSON object per line in api_log.jsonl (path configurable with API_LOG_PATH):
    {"ts": ..., "service": ..., "endpoint": ..., "status": ..., "latency_ms": ...,
     "error": ..., "request_bytes": ..., "response_bytes": ...}

Use it directly for the API-usage section of the README:
    python -c "import json;[print(json.dumps(json.loads(l))) for l in open(f'{__import__('pathlib').Path(__file__).parent}/backend/api_log.jsonl')]"
"""
import json
import os
import sys
import threading
import time
from pathlib import Path

from config import backend_dir, get

_lock = threading.Lock()


def _log_path() -> Path:
    configured = get("API_LOG_PATH")
    if configured:
        p = Path(configured)
        return p if p.is_absolute() else backend_dir() / p
    return backend_dir() / "api_log.jsonl"


def log_call(
    service: str,
    endpoint: str,
    status: str,
    latency_ms: float,
    error: str | None = None,
    request_bytes: int | None = None,
    response_bytes: int | None = None,
    status_code: int | None = None,
) -> None:
    """Append one line. Never raises: logging must not break the conversation."""
    record = {
        "ts": round(time.time(), 3),
        "service": service,
        "endpoint": endpoint,
        "status": status,
        "latency_ms": round(latency_ms, 1),
        "error": error,
        "request_bytes": request_bytes,
        "response_bytes": response_bytes,
    }
    if status_code is not None:
        record["status_code"] = status_code
    line = json.dumps(record, ensure_ascii=False)
    try:
        with _lock:
            path = _log_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception as exc:  # noqa: BLE001 - logging failures are swallowed
        print(f"[apilog] could not write log line: {exc}", file=sys.stderr)


if __name__ == "__main__":
    log_call("selftest", "noop", "ok", 1.0)
    print(f"wrote one selftest line to {_log_path()}")
