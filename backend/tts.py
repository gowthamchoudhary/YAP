"""The ONLY file in the project that talks to AssemblyAI for speech output.

AssemblyAI has NO standalone text-to-speech HTTP endpoint. Speech output exists
only inside the Voice Agent API — a WebSocket session (STT + LLM + TTS bundled
together) at wss://agents.assemblyai.com/v1/ws. The one supported way to make
the agent speak EXACTLY the text we supply is the session's `greeting` field,
which is spoken verbatim as soon as the session is ready (no user audio needed).

So `synthesize(text, voice_id, style=None)` now:
  1. opens a NEW Voice Agent WebSocket session, auth = Bearer ASSEMBLYAI_API_KEY
     in the Authorization header of the upgrade request (per the Voice Agent
     WebSocket API reference),
  2. sends as its first message:
       {"type": "session.update",
        "session": {"greeting": <text>,
                    "output": {"voice": <voice_id>,
                               "format": {"encoding": "audio/pcm"}}}}
  3. waits for `session.ready`, then collects the base64 `reply.audio` chunks
     (PCM16, 24 kHz, little-endian, mono per the audio-format docs) until the
     documented reply-completion event `reply.done` arrives,
  4. immediately sends `{"type": "session.end"}` and waits for `session.ended`,
     so the agent's own LLM loop can never engage — we only wanted the greeting
     spoken (we never send `input.audio`, and ending the session also avoids
     the billable 30-second resume grace window a bare close would leave),
  5. returns the captured audio bytes with mime "audio/pcm", plus the style
     method ("text" / "none") as the third element of the tuple.

Each spoken line is therefore ONE billable session, not a lightweight REST call.
Session open time, time-to-first-audio-byte and total time are logged through
apilog.py (services tts-session-open / tts-first-audio / tts) so the per-line
overhead can be measured — see ASSEMBLYAI_NOTES.md.

Delivery styling stays text-level only (punctuation/pauses via shape_text);
the Voice Agent session exposes no per-delivery style control, and nothing here
invents audio effects. Language selection remains gated by
backend/tts_capabilities.json (see load_capabilities).

Environment variables it reads:
  ASSEMBLYAI_API_KEY   (required)
  ASSEMBLYAI_TTS_URL   (optional override; default is the documented endpoint)
  TTS_DISABLED=1       (engine runs with no audio at all)
"""
import asyncio
import base64
import hashlib
import json
import re
import sys
import time
from pathlib import Path

import apilog
from config import backend_dir, get, get_bool

CACHE_DIR = backend_dir() / ".tts_cache"
CAPABILITIES_PATH = backend_dir() / "tts_capabilities.json"
TTS_WS_URL = "wss://agents.assemblyai.com/v1/ws"

_CAPS_CACHE: dict | None = None

# Session timing budgets (each line = one billable session, so keep them tight)
SESSION_READY_TIMEOUT_S = 10.0    # session.update -> session.ready
SESSION_TOTAL_TIMEOUT_S = 30.0    # hard cap on one greeting session
GREETING_QUIET_FINISH_S = 2.5     # safety net if reply.done never arrives
SESSION_END_WAIT_S = 2.0          # session.end -> session.ended


class TTSNotConfigured(Exception):
    """Raised when TTS cannot run; the message names exactly what is missing."""


def is_configured() -> bool:
    if get_bool("TTS_DISABLED", False):
        return False
    return bool(get("ASSEMBLYAI_API_KEY"))


def not_configured_error() -> TTSNotConfigured:
    return TTSNotConfigured(
        "TTS is not configured — add ASSEMBLYAI_API_KEY in backend/.env "
        "(or set TTS_DISABLED=1 to run text-only)."
    )


# ------------------------------------------------------------------ capabilities
def load_capabilities() -> dict:
    """backend/tts_capabilities.json -> dict (cached; missing file = documented defaults).

    Still the user's fill-in point for the language list. Native per-delivery
    audio params do not exist in the Voice Agent session config, so the
    `deliveries` map is informational here; delivery always shapes text.
    """
    global _CAPS_CACHE
    if _CAPS_CACHE is not None:
        return _CAPS_CACHE
    caps: dict = {"deliveries": {}, "languages_supported": False, "languages": []}
    try:
        raw = json.loads(CAPABILITIES_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            caps["deliveries"] = {
                str(k): v for k, v in (raw.get("deliveries") or {}).items() if isinstance(v, dict)
            }
            caps["languages_supported"] = bool(raw.get("languages_supported"))
            langs = raw.get("languages")
            if isinstance(langs, list):
                caps["languages"] = [str(x) for x in langs]
    except (OSError, ValueError) as exc:
        print(f"[tts] tts_capabilities.json unreadable ({exc}); defaults apply", file=sys.stderr)
    _CAPS_CACHE = caps
    return caps


def reload_capabilities() -> dict:
    global _CAPS_CACHE
    _CAPS_CACHE = None
    return load_capabilities()


# ------------------------------------------------------------------ text shaping
_PAD = re.compile(r"[.?!](?=\s|$)")
_NERV = re.compile(r"[.?]+")


def shape_text(text: str, delivery: str | None) -> str:
    """Text-level delivery shaping: punctuation and pauses ONLY.

    The Voice Agent session exposes no native style control, so shaping is the
    only hint a delivery gets — never a fabricated audio effect.
    """
    t = (text or "").strip()
    if not t or not delivery:
        return t
    if delivery == "whisper":
        return _PAD.sub("...", t)
    if delivery == "shout":
        t2 = t.rstrip(".…?!)$'")
        return (t2 + "!" if t2 else t).upper()
    if delivery == "nervous":
        t2 = _NERV.sub("... ", t).rstrip()
        return t2 if t2.endswith(("...", ".")) else (t2 + "..." if t2 else text)
    if delivery == "slow":
        return t.replace(". ", ". ... ")
    if delivery == "fast":
        return t.replace(", ", " ")
    if delivery == "sarcastic":
        t2 = t.rstrip(".…?!)$'")
        return (t2 + "..." if t2 else t)
    return t


def _cache_key(text: str, voice_id: str) -> str:
    return hashlib.sha1(f"{voice_id}\x00{text}".encode("utf-8")).hexdigest()


def _cache_paths(text: str, voice_id: str) -> tuple[Path, Path]:
    base = CACHE_DIR / _cache_key(text, voice_id)
    return base.with_suffix(".bin"), base.with_suffix(".mime")


def _cache_read(text: str, voice_id: str) -> tuple[bytes, str] | None:
    bin_path, mime_path = _cache_paths(text, voice_id)
    try:
        if bin_path.exists() and mime_path.exists():
            mime = mime_path.read_text(encoding="utf-8").strip()
            if mime:
                apilog.log_call(service="tts-cache", endpoint="disk", status="ok", latency_ms=0.0)
                return bin_path.read_bytes(), mime
    except OSError:
        return None
    return None


def _cache_write(text: str, voice_id: str, audio: bytes, mime: str) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        bin_path, mime_path = _cache_paths(text, voice_id)
        bin_path.write_bytes(audio)
        mime_path.write_text(mime, encoding="utf-8")
    except OSError as exc:
        print(f"[tts] cache write failed (non-fatal): {exc}", file=sys.stderr)


# ------------------------------------------------------- ONE greeting session
_last_timings: dict = {}  # {"open_ms", "ttfa_ms", "total_ms", "audio_bytes"} of the LAST session


async def _speak_via_session(text: str, voice_id: str) -> bytes:
    """One Voice Agent session whose greeting speaks `text` verbatim.

    Protocol (per the Voice Agent docs, none invented):
      connect (Authorization: Bearer <key>) -> send session.update with
      session.greeting + session.output.voice ('audio/pcm' is the documented
      default encoding: PCM16 / 24 kHz / mono) -> server sends session.ready ->
      the greeting is spoken immediately (no input audio required) ->
      reply.started -> base64 reply.audio chunks -> reply.done ->
      we send session.end and wait for session.ended.
    """
    global _last_timings
    import websockets

    url = get("ASSEMBLYAI_TTS_URL", TTS_WS_URL) or TTS_WS_URL
    key = get("ASSEMBLYAI_API_KEY") or ""
    t0 = time.perf_counter()
    ready_at: float | None = None
    first_audio_at: float | None = None
    audio = bytearray()
    ws = None
    status, err = "ok", None
    try:
        ws = await websockets.connect(
            url,
            additional_headers={"Authorization": f"Bearer {key}"},
            max_size=None,
            open_timeout=SESSION_READY_TIMEOUT_S,
        )
        open_ms = (time.perf_counter() - t0) * 1000
        apilog.log_call(service="tts-session-open", endpoint=url, status="ok", latency_ms=open_ms)

        await ws.send(
            json.dumps(
                {
                    "type": "session.update",
                    "session": {
                        "greeting": text,
                        "output": {"voice": voice_id, "format": {"encoding": "audio/pcm"}},
                    },
                }
            )
        )

        deadline = time.perf_counter() + SESSION_TOTAL_TIMEOUT_S
        last_chunk_at = time.perf_counter()
        ready = False
        started = False
        done = False
        quiet_done = False
        while not done and not quiet_done:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                if audio or ready or started:
                    # safety net tripped; we have the greeting, end the session
                    break
                raise RuntimeError(
                    f"Voice Agent session produced no audio within {SESSION_TOTAL_TIMEOUT_S:.0f}s."
                )
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=min(remaining, GREETING_QUIET_FINISH_S))
            except asyncio.TimeoutError:
                # quiet for GREETING_QUIET_FINISH_S with audio in hand: treat as end
                if ready and (audio or started) and (time.perf_counter() - last_chunk_at) >= GREETING_QUIET_FINISH_S:
                    quiet_done = True
                    print("[tts] reply ended on inactivity (reply.done not seen)", file=sys.stderr)
                    break
                continue
            if isinstance(raw, (bytes, bytearray)):
                continue  # unexpected binary frame; documented audio rides in JSON events
            msg = json.loads(raw)
            mtype = msg.get("type")
            if mtype == "session.ready":
                ready = True
                ready_at = time.perf_counter()
                open_ready_ms = (ready_at - t0) * 1000
                apilog.log_call(
                    service="tts-session-ready", endpoint=url, status="ok", latency_ms=open_ready_ms
                )
            elif mtype == "reply.audio":
                data = msg.get("data")
                if data:
                    chunk = base64.b64decode(data)
                    if chunk:
                        if first_audio_at is None:
                            first_audio_at = time.perf_counter()
                            apilog.log_call(
                                service="tts-first-audio",
                                endpoint=url,
                                status="ok",
                                latency_ms=(first_audio_at - t0) * 1000,
                            )
                        audio.extend(chunk)
                        last_chunk_at = time.perf_counter()
            elif mtype == "reply.started":
                started = True
            elif mtype == "reply.done":
                done = True
            elif mtype == "session.ended" and not any([audio, started]):
                raise RuntimeError("session.ended before any greeting audio arrived.")
            elif isinstance(mtype, str) and "error" in mtype:
                raise RuntimeError(
                    f"Voice Agent error event '{mtype}': "
                    f"{json.dumps(msg.get('error') or msg)[:200]}"
                )
        total_ms = (time.perf_counter() - t0) * 1000
        if not audio:
            raise RuntimeError("Voice Agent session ended without any reply.audio — no audio captured.")

        # ---- terminate immediately: no LLM loop, no billable resume grace window
        if ws is not None:
            await _end_session(ws)

        _last_timings = {
            "open_ms": round(open_ms, 1),
            "ttfa_ms": round(((first_audio_at or time.perf_counter()) - t0) * 1000, 1),
            "total_ms": round(total_ms, 1),
            "audio_bytes": len(audio),
        }
        apilog.log_call(
            service="tts",
            endpoint=url,
            status=status,
            latency_ms=total_ms,
            response_bytes=len(audio),
        )
        return bytes(audio)
    except Exception as exc:  # noqa: BLE001 - every failure becomes an engine error event
        err = f"{type(exc).__name__}: {exc}"
        apilog.log_call(
            service="tts",
            endpoint=url,
            status="error",
            latency_ms=(time.perf_counter() - t0) * 1000,
            error=err[:200],
        )
        print(f"[tts] session failed: {err}", file=sys.stderr)
        if ws is not None:
            try:
                await _end_session(ws)
            except Exception:  # noqa: BLE001
                pass
        raise RuntimeError(f"Voice Agent TTS session failed: {err}") from exc


async def _end_session(ws) -> None:
    """session.end -> session.ended (the documented termination, not a bare close)."""
    try:
        await ws.send(json.dumps({"type": "session.end"}))
        end_deadline = time.perf_counter() + SESSION_END_WAIT_S
        while time.perf_counter() < end_deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(0.05, end_deadline - time.perf_counter()))
                if isinstance(raw, (bytes, bytearray)):
                    continue
                if json.loads(raw).get("type") == "session.ended":
                    break
            except asyncio.TimeoutError:
                break
    finally:
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass


# ------------------------------------------------------------------ entrypoint
async def synthesize(text: str, voice_id: str, style: dict | None = None) -> tuple[bytes, str, str]:
    """text + voice id + optional style dict -> (audio_bytes, mime, style_method).

    Disk-cached by sha1(text+voice) so repeated lines never reopen a session.
    style_method: "text" when shape_text reshaped the text for the delivery,
    "none" when the text goes in verbatim (the session exposes no native style
    control, so no other method exists and none is invented).
    Raises TTSNotConfigured when the API key is missing / TTS disabled.
    """
    if get_bool("TTS_DISABLED", False):
        raise TTSNotConfigured("TTS_DISABLED=1 is set; TTS audio is intentionally off.")
    if not get("ASSEMBLYAI_API_KEY"):
        raise TTSNotConfigured(
            "Missing required environment variable: ASSEMBLYAI_API_KEY in backend/.env."
        )

    style = style if isinstance(style, dict) else {}
    delivery = str(style.get("delivery") or "") or None
    # a set language is intentionally not honoured: the greeting workaround has
    # no language control; the engine's language gate (capabilities json) keeps
    # the selector from ever reaching this path with an unsupported code.
    text = shape_text(text, delivery) if delivery else text
    style_method = "text" if delivery else "none"

    cached = _cache_read(text, voice_id)
    if cached is not None:
        return cached[0], cached[1], style_method

    audio = await _speak_via_session(text, voice_id)
    mime = "audio/pcm"
    _cache_write(text, voice_id, audio, mime)
    return audio, mime, style_method


async def warm_up() -> None:
    """Called once at FastAPI startup. Fail-safe: only logs what ails TTS."""
    if get_bool("TTS_DISABLED", False):
        print("[tts] TTS_DISABLED=1 -> warm-up skipped", file=sys.stderr)
        return
    if not is_configured():
        print(f"[tts] warm-up: {not_configured_error()}", file=sys.stderr)
        return
    try:
        await synthesize("Warm-up sentence.", load_default_voice())
    except Exception as exc:  # noqa: BLE001 - warm-up must not crash startup
        print(f"[tts] warm-up failed (non-fatal): {exc}", file=sys.stderr)


def load_default_voice() -> str:
    """First voice from voices.json — used only by warm_up() and tests."""
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
        caps = load_capabilities()
        print("capabilities:", caps)
        try:
            audio, mime, method = await synthesize(
                "This is a test from the Living World.", load_default_voice()
            )
            print("TTS OK", mime, method, len(audio), "bytes |", _last_timings)
        except TTSNotConfigured as exc:
            print(f"TTS not configured: {exc}")
        except Exception as exc:  # noqa: BLE001
            print(f"TTS failed: {exc}")

    asyncio.run(_main())
