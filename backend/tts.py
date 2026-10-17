"""The ONLY file in the project that talks to AssemblyAI TTS.

`synthesize(text, voice_id) -> (audio_bytes, mime_type)` is the stable contract
used by engine.py. The HTTP call itself is NOT implemented yet, because
AssemblyAI has not published a documented standalone TTS HTTP endpoint. Paste
your TTS snippet at the clearly marked block below; everything around it
(keep-alive client, disk cache keyed by sha1(text+voice), 5xx retry, warm-up,
timing log) is already implemented and waiting.

Environment variables it reads:
  ASSEMBLYAI_API_KEY          (required)
  ASSEMBLYAI_TTS_URL          (required; leave empty until docs are public)
  ASSEMBLYAI_TTS_VOICE_PARAM  (name of the voice field in the request body;
                               default from .env.example: voice_id)
  TTS_DISABLED=1              (engine runs with no audio at all; used by
                               scripts/test_engine_text_only.py)
"""
import hashlib
import sys
import time
from pathlib import Path

import httpx

import apilog
from config import backend_dir, get, get_bool

CACHE_DIR = backend_dir() / ".tts_cache"


class _Retryable5xx(Exception):
    """Raised by _call_assemblyai_tts on HTTP 5xx so synthesize() retries once."""


class TTSNotConfigured(Exception):
    """Raised when the TTS snippet was not pasted or env vars are missing.

    Message always names exactly what to add/paste next.
    """


def _snippet_pasted() -> bool:
    # flips to True by `_call_assemblyai_tts` below once a real implementation exists
    return getattr(_call_assemblyai_tts, "_implemented", False)


def is_configured() -> bool:
    if get_bool("TTS_DISABLED", False):
        return False
    if not _snippet_pasted():
        return False
    return bool(get("ASSEMBLYAI_TTS_URL")) and bool(get("ASSEMBLYAI_API_KEY"))


def not_configured_error() -> TTSNotConfigured:
    missing = []
    if not _snippet_pasted():
        missing.append(
            "the TTS snippet pasted into backend/tts.py at 'PASTE ASSEMBLYAI TTS SNIPPET HERE'"
        )
    if not get("ASSEMBLYAI_API_KEY"):
        missing.append("ASSEMBLYAI_API_KEY in backend/.env")
    if not get("ASSEMBLYAI_TTS_URL"):
        missing.append("ASSEMBLYAI_TTS_URL in backend/.env")
    return TTSNotConfigured(
        "TTS is not configured yet — add: " + "; and ".join(missing) + "."
    )


_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Keep-alive client reused for every synthesis call."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0), limits=httpx.Limits(max_connections=8)
        )
    return _client


def _cache_key(text: str, voice_id: str) -> str:
    return hashlib.sha1(f"{voice_id}\x00{text}".encode("utf-8")).hexdigest()


def _cache_path(text: str, voice_id: str, ext: str) -> Path:
    return CACHE_DIR / f"{_cache_key(text, voice_id)}.{ext}"


_MIME_EXT = {"audio/mpeg": "mp3", "audio/mp3": "mp3", "audio/wav": "wav",
             "audio/x-wav": "wav", "audio/ogg": "ogg", "audio/pcm": "pcm"}


async def synthesize(text: str, voice_id: str) -> tuple[bytes, str]:
    """text + voice id -> (audio_bytes, mime_type). Disk-cached, 1 retry on 5xx.

    Raises TTSNotConfigured (with the exact missing setup in the message) when
    the snippet/env is not in place yet.
    """
    if get_bool("TTS_DISABLED", False):
        raise TTSNotConfigured("TTS_DISABLED=1 is set; TTS audio is intentionally off.")

    if not _snippet_pasted():
        raise not_configured_error()
    for var in ("ASSEMBLYAI_API_KEY", "ASSEMBLYAI_TTS_URL"):
        if not get(var):
            raise TTSNotConfigured(f"Missing required environment variable: {var} in backend/.env.")

    # ---- disk cache first
    probe_ext = "mp3"
    cached = _cache_path(text, voice_id, probe_ext)
    if cached.exists():
        cached = _cache_path(text, voice_id, cached.read_bytes()[:4] == b"RIFF" and "wav" or "mp3")
    for ext, mime in _MIME_EXT.items():
        p = _cache_path(text, voice_id, ext)
        if p.exists():
            apilog.log_call(service="tts-cache", endpoint="disk", status="ok", latency_ms=0.0)
            return p.read_bytes(), ext_to_mime(ext)

    t0 = time.perf_counter()
    audio, mime, status_code = await _synthesize_with_retry(text, voice_id)
    latency = (time.perf_counter() - t0) * 1000
    ext = _MIME_EXT.get(mime, "bin")
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(text, voice_id, ext).write_bytes(audio)
    except OSError as exc:
        print(f"[tts] cache write failed (non-fatal): {exc}", file=sys.stderr)
    print(f"[tts] voice={voice_id} chars={len(text)} latency={latency:.0f}ms mime={mime}", file=sys.stderr)
    return audio, mime


def ext_to_mime(ext: str) -> str:
    for mime, e in _MIME_EXT.items():
        if e == ext:
            return mime
    return "application/octet-stream"


async def _synthesize_with_retry(text: str, voice_id: str) -> tuple[bytes, str, int]:
    last_exc: Exception | None = None
    for attempt in (1, 2):  # retry once on 5xx
        try:
            return await _call_assemblyai_tts(text, voice_id)
        except _Retryable5xx as exc:
            last_exc = exc
            print(f"[tts] 5xx (attempt {attempt}); retry: {exc}", file=sys.stderr)
    raise RuntimeError(f"AssemblyAI TTS failed after retry: {last_exc}")


# ============================================================================
# PASTE ASSEMBLYAI TTS SNIPPET HERE
#
# TODO: AssemblyAI has NOT published a documented standalone text-to-speech
# HTTP endpoint yet (its speech output currently rides on the Voice Agent API
# websocket, which is not a text->audio REST call). Once you have the official
# TTS request shape (from the AssemblyAI TTS docs), replace the function below
# with the real implementation and set `_implemented = True` on it.
#
# Contract enforced here: an async function
#     _call_assemblyai_tts(text: str, voice_id: str) -> (audio_bytes, mime_type)
# that:
#   - uses module client `_get_client()` (keep-alive),
#   - sends ONE json body incl. {"<ASSEMBLYAI_TTS_VOICE_PARAM>": voice_id, "text": text},
#   - on 5xx raises _Retryable5xx(str(resp.status_code)) so synthesize() retries once,
#   - on any other httpx error raises RuntimeError,
#   - on success apilog.log_call(...) is already done by _synthesize_with_retry
#     via the timing in synthesize() — no duplicate logging needed,
#   - returns the raw response body and its response content-type string
#     (e.g. "audio/mpeg"); raw PCM responses should return "audio/pcm".
#
# Until this block is implemented, synthesize() raises TTSNotConfigured that
# names exactly what is missing — NO fake fallback audio is ever produced here.
# ============================================================================
async def _call_assemblyai_tts(text: str, voice_id: str) -> tuple[bytes, str, int]:
    _call_assemblyai_tts._implemented = False  # type: ignore[attr-defined]
    raise not_configured_error()


async def warm_up() -> None:
    """Called once at FastAPI startup. Fails-safe: only logs what ails TTS."""
    if get_bool("TTS_DISABLED", False):
        print("[tts] TTS_DISABLED=1 -> warm-up skipped", file=sys.stderr)
        return
    if not is_configured():
        print(f"[tts] warm-up: {not_configured_error()}", file=sys.stderr)
        return
    try:
        t0 = time.perf_counter()
        await synthesize("Warm-up sentence.", load_default_voice())
        apilog.log_call(
            service="tts-warmup",
            endpoint=str(get("ASSEMBLYAI_TTS_URL")),
            status="ok",
            latency_ms=(time.perf_counter() - t0) * 1000,
        )
    except Exception as exc:  # noqa: BLE001 - warm-up must not crash startup
        apilog.log_call(
            service="tts-warmup",
            endpoint=str(get("ASSEMBLYAI_TTS_URL")),
            status="error",
            latency_ms=0.0,
            error=str(exc)[:200],
        )
        print(f"[tts] warm-up failed: {exc}", file=sys.stderr)


def load_default_voice() -> str:
    """First voice from voices.json — used only by warm_up()."""
    import json

    path = backend_dir() / "voices.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    raw = data.get("voices") if isinstance(data, dict) else data
    for item in raw or []:
        if isinstance(item, str):
            return item
        if isinstance(item, dict) and item.get("id"):
            return item["id"]
    raise RuntimeError("voices.json contains no voices.")


if __name__ == "__main__":
    import asyncio

    async def _main() -> None:
        try:
            await synthesize("This is a test from the Living World.", load_default_voice())
            print("TTS OK")
        except TTSNotConfigured as exc:
            print(f"TTS not configured, as expected until the snippet is pasted: {exc}")

    asyncio.run(_main())
