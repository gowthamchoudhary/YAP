"""Streaming STT test. Usage: python scripts/test_stt.py path/to/audio.wav [backend_url]

Reads a 16 kHz mono 16-bit PCM WAV, opens /ws/stt on the running FastAPI backend,
streams ~50 ms frames, and prints the partial/final transcript events with timing.
The backend then relays to the real AssemblyAI streaming endpoint.

  1) start the backend:  cd backend && python -m uvicorn main:app --port 8000
  2) run:                python scripts/test_stt.py myvoice.wav
"""
import asyncio
import json
import sys
import time
import wave
from pathlib import Path

import websockets

BACKEND = sys.argv[2] if len(sys.argv) > 2 else "ws://localhost:8000"


def read_wav(path: Path) -> tuple[bytes, int]:
    with wave.open(str(path), "rb") as w:
        rate = w.getframerate()
        frames = w.getnframes()
        audio = w.readframes(frames)
    return audio, rate


async def main() -> None:
    if len(sys.argv) < 2 or not Path(sys.argv[1]).exists():
        print("usage: python scripts/test_stt.py path/to/audio.wav [backend_url]")
        sys.exit(2)
    audio, rate = read_wav(Path(sys.argv[1]))
    if rate != 16000:
        print(f"this test needs a 16 kHz mono wav (got {rate} Hz). Any TTS/TTS tool can resample:")
        print("  ffmpeg -i input.wav -ar 16000 -ac 1 input16k.wav")
        sys.exit(2)
    # 50 ms frames of 16000 samples/s * 2 bytes = 1600 bytes (matches docs)
    chunk = 1600
    t_conn = time.perf_counter()
    async with websockets.connect(f"{BACKEND}/ws/stt", max_size=None) as ws:
        print(f"connected to {BACKEND}/ws/stt in {(time.perf_counter() - t_conn) * 1000:.0f} ms")
        t_start = time.perf_counter()

        async def sender() -> None:
            for i in range(0, len(audio), chunk):
                await ws.send(audio[i : i + chunk])
                await asyncio.sleep(0.05)
            await ws.send('{"type":"stop"}')

        send_task = asyncio.create_task(sender())
        n_events = 0
        try:
            while True:
                ev = await asyncio.wait_for(ws.recv(), timeout=12)
                n_events += 1
                msg = json.loads(ev)
                at = (time.perf_counter() - t_start) * 1000
                if msg.get("type") == "partial":
                    print(f" {at:8.0f}ms PARTIAL: {msg.get('text')}")
                elif msg.get("type") == "final":
                    print(f" {at:8.0f}ms FINAL:   {msg.get('text')}")
                elif msg.get("type") == "ready":
                    print(f" {at:8.0f}ms session ready")
                elif msg.get("type") == "end":
                    print(f" {at:8.0f}ms session end")
                    break
                elif msg.get("type") == "error":
                    print(f" {at:8.0f}ms ERROR:   {msg}")
                    break
        except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed):
            pass
        send_task.cancel()
        print(f"total transcript events: {n_events}")


if __name__ == "__main__":
    asyncio.run(main())
