"""Real TTS test. Usage: python scripts/test_tts.py

Synthesizes 3 short lines with 3 different voices and saves the audio next to
this script as test_tts_out/<voice>_<n>.<ext>. If TTS is not configured yet it
prints exactly what is missing (the paste-block / env vars) instead of faking it.
"""
import asyncio
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, HERE.parent / "backend")

import characters  # for load_voices
import tts  # noqa: E402

LINES = [
    "The stairs only need one clumsy moment and a very convincing creak.",
    "I would never underestimate a desk lamp with motives.",
    "We were only discussing how efficiently I pour coffee.",
]


async def main() -> None:
    voices = characters.load_voices()[:3]
    print(f"voices under test: {voices}")
    out_dir = HERE / "test_tts_out"
    out_dir.mkdir(exist_ok=True)
    ok = True
    for li, line in enumerate(LINES):
        for vi, voice in enumerate(voices):
            t0 = time.perf_counter()
            try:
                audio, mime = await tts.synthesize(line, voice)
            except tts.TTSNotConfigured as exc:
                print(f"NOT CONFIGURED: {exc}")
                print("  fill ASSEMBLYAI_API_KEY + ASSEMBLYAI_TTS_URL in backend/.env")
                print("  and paste the real request shape into backend/tts.py at the")
                print("  'PASTE ASSEMBLYAI TTS SNIPPET HERE' block, then set _implemented = True.")
                sys.exit(1)
            except Exception as exc:  # noqa: BLE001
                print(f"FAILED voice={voice}: {exc}")
                ok = False
                continue
            ms = (time.perf_counter() - t0) * 1000
            name = out_dir / f"{voice}_{li}.{tts.ext_to_mime(mime).replace('audio/', '')}"
            name.write_bytes(audio)
            print(f"voice={voice!r:10} line={li} latency={ms:6.0f}ms bytes={len(audio):6d} mime={mime} -> {name.name}")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
