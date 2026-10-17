"""Streaming speech-to-text proxy: browser <-> AssemblyAI Universal Streaming v3.

The browser connects to /ws/stt and streams raw mono 16-bit PCM @16 kHz as
binary websocket frames. This module relays them upstream to
    wss://streaming.assemblyai.com/v3/ws?speech_model=universal-streaming-english&encoding=pcm_s16le&sample_rate=16000
with the API key in the `Authorization` header (WITHOUT the "Bearer" prefix,
per the AssemblyAI streaming docs), and forwards transcripts back as:

    {"type": "partial", "text": "..."}     every Turn with end_of_turn=false
    {"type": "final",   "text": "..."}     every Turn with end_of_turn=true
    {"type": "ready"}
    {"type": "error",   "code": ..., "message": "..."}

The API key NEVER reaches the browser.

Message shapes verified against:
  https://www.assemblyai.com/docs/streaming/api-spec/streaming-websocket
  https://www.assemblyai.com/docs/streaming/message-sequence
"""
import json
import sys
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

import apilog
from config import get, require

UPSTREAM_URL = get(
    "ASSEMBLYAI_STT_URL",
    "wss://streaming.assemblyai.com/v3/ws?speech_model=universal-streaming-english&encoding=pcm_s16le&sample_rate=16000",
) or ""

# Audio frame constraints from the AssemblyAI session-error docs:
MIN_CHUNK_MS, MAX_CHUNK_MS = 50, 1000
BYTES_PER_MS = 32  # 16 kHz * 2 bytes


async def _upstream_reader(upstream, client: WebSocket, session_meta: dict[str, Any]) -> None:
    """Pull server messages and relay transcripts to the browser."""
    got_turn_final = False
    try:
        async for raw in upstream:
            if isinstance(raw, (bytes, bytearray)):
                continue
            msg = json.loads(raw)
            mtype = msg.get("type")
            if mtype == "Begin":
                session_meta["id"] = msg.get("id")
                print(f"[stt] upstream session Begin id={msg.get('id')} config={msg.get('configuration')}", file=sys.stderr)
                await client.send_json({"type": "ready"})
            elif mtype == "Turn":
                text = (msg.get("transcript") or "").strip()
                if not text:
                    continue
                if msg.get("end_of_turn"):
                    if not got_turn_final:
                        got_turn_final = True
                        await client.send_json({"type": "final", "text": text})
                else:
                    await client.send_json({"type": "partial", "text": text})
            elif mtype == "Termination":
                print("[stt] upstream Termination", file=sys.stderr)
                await client.send_json({"type": "end"})
                break
            elif mtype == "Error":
                await client.send_json(
                    {"type": "error", "code": msg.get("error_code"), "message": str(msg.get("error"))[:300]}
                )
    except Exception as exc:  # noqa: BLE001
        if upstream.client_state.name != "CLOSED" if hasattr(upstream, 'client_state') else True:
            print(f"[stt] upstream read error: {exc}", file=sys.stderr)
        try:
            await client.send_json({"type": "error", "code": "upstream", "message": str(exc)[:300]})
        except Exception:  # noqa: BLE001
            pass


async def _browser_listener(browser: WebSocket, upstream, session_meta: dict[str, Any]) -> None:
    """Relay browser audio frames upstream; handle the Terminate request."""
    ms_buffered = 0.0
    try:
        while True:
            frame = await browser.receive()
            if "bytes" in frame and frame["bytes"]:
                chunk = frame["bytes"]
                ms = len(chunk) / BYTES_PER_MS
                if ms < MIN_CHUNK_MS or ms > MAX_CHUNK_MS:
                    # clamp silently: AssemblyAI rejects frames outside 50..1000 ms
                    pass
                ms_buffered += ms
                await upstream.send(chunk)
            elif "text" in frame and frame["text"]:
                data = json.loads(frame["text"])
                if data.get("type") == "stop":
                    await upstream.send(json.dumps({"type": "Terminate"}))
                elif data.get("type") == "ping":
                    await browser.send_json({"type": "pong"})
    except (WebSocketDisconnect, Exception) as exc:  # noqa: BLE001
        if isinstance(exc, WebSocketDisconnect) or "disconnect" in str(exc).lower():
            print(f"[stt] browser disconnected ({ms_buffered:.0f} ms sent)", file=sys.stderr)
        else:
            print(f"[stt] browser listener error: {exc}", file=sys.stderr)
        session_meta["interrupted"] = True


async def handle_websocket(browser: WebSocket) -> None:
    await browser.accept()
    t0 = __import__("time").perf_counter()
    session_meta: dict[str, Any] = {}
    try:
        key = require(
            "ASSEMBLYAI_API_KEY",
            "Streaming STT needs it. Add it in backend/.env (no Bearer prefix, header only).",
        )
    except Exception as exc:  # noqa: BLE001
        await browser.send_json({"type": "error", "code": "missing_env", "message": str(exc)})
        await browser.close(code=1003)
        return
    if not UPSTREAM_URL:
        await browser.send_json(
            {"type": "error", "code": "config", "message": "ASSEMBLYAI_STT_URL resolved to an empty URL."}
        )
        await browser.close(code=1003)
        return

    import websockets  # project dependency 'websockets'

    try:
        # Authorization header carries the RAW key, no Bearer prefix (per docs).
        upstream = await websockets.connect(
            UPSTREAM_URL,
            additional_headers={"Authorization": key},
            max_size=None,
            open_timeout=10,
        )
    except Exception as exc:  # noqa: BLE001
        await browser.send_json(
            {"type": "error", "code": "upstream_connect", "message": f"Could not reach AssemblyAI streaming: {exc}"[:300]}
        )
        apilog.log_call(
            service="assemblyai-stt", endpoint=UPSTREAM_URL.split("?")[0], status="error",
            latency_ms=0.0, error=str(exc)[:200],
        )
        await browser.close(code=1011)
        return

    reader_task = asyncio_create_task(_upstream_reader(upstream, browser, session_meta))
    try:
        await _browser_listener(browser, upstream, session_meta)
    finally:
        # give upstream a beat to flush any final transcript
        if not session_meta.get("interrupted"):
            try:
                await upstream.send(json.dumps({"type": "Terminate"}))
                await asyncio_wait_for_or_none(reader_task, 2.0)
            except Exception:  # noqa: BLE001
                pass
        reader_task.cancel()
        await upstream.close()
        apilog.log_call(
            service="assemblyai-stt",
            endpoint=UPSTREAM_URL.split("?")[0],
            status="ok",
            latency_ms=(__import__("time").perf_counter() - t0) * 1000,
        )


def asyncio_create_task(coro):
    import asyncio

    return asyncio.create_task(coro)


async def asyncio_wait_for_or_none(task, timeout: float):
    import asyncio

    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    print("Run via FastAPI: uvicorn main:app, then connect ws://localhost:8000/ws/stt")
