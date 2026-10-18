"""Real TTS test. Usage: python scripts/test_tts.py

Opens 3 REAL Voice Agent sessions (one per voice, 3 short lines) and prints
session-open-to-first-audio latency for each — because every synthesized line
is now one full billable wss://agents.assemblyai.com/v1/ws session whose
greeting speaks exactly our text. Audio is saved next to this script under
test_tts_out/<voice>_0.pcm (raw PCM16, 24 kHz mono).
"""
import asyncio
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "backend"))

import characters  # for load_voices
import tts  # noqa: E402

# 3 distinct short lines so the cache reveals whether a repeated (text, voice)
# pair ever reopens a session (it should not — sha1(text+voice) is cached).
LINES = [
    "We were only discussing how efficiently I pour coffee.",
    "One cup at a time, always the right cup.",
    "Efficiency is not a guess, it is a pour.",
]

VOICES_TO_USE = 3


async def main() -> None:
    voices = characters.load_voices()[:VOICES_TO_USE]
    print(f"voices under test: {voices}")
    out_dir = HERE / "test_tts_out"
    out_dir.mkdir(exist_ok=True)

    # 3 sessions x 3 lines == 9 sessions, but identical (text, voice) pairs
    # are served from disk cache and never reopen a session.
    ok = True
    idx = 0
    for vi, voice in enumerate(voices):
        for li, text in enumerate(LINES):
            try:
                audio, mime, style_method = await tts.synthesize(text, voice)
            except tts.TTSNotConfigured as exc:
                print(f"NOT CONFIGURED: {exc}")
                print("  add ASSEMBLYAI_API_KEY in backend/.env")
                sys.exit(1)
            except Exception as exc:  # noqa: BLE001
                print(f"FAILED voice={voice!r} line={li}: {exc}")
                ok = False
                continue
            timing = tts._last_timings
            name = out_dir / f"line{idx:02d}_{voice}.pcm"
            name.write_bytes(audio)
            print(
                f"[{name.name}] voice={voice!r:10} line={li} "
                f"open={timing.get('open_ms', -1):6.0f}ms "
                f"open->first-audio={timing.get('ttfa_ms', -1):6.0f}ms "
                f"total={timing.get('total_ms', -1):6.0f}ms "
                f"bytes={len(audio):6d} mime={mime} style={style_method}"
            )
            idx += 1

    # explicit cache check: re-request the first line + first voice; that must
    # be a cache hit (response_bytes 0 in the tts log row) and not reopen a session.
    print("\n--- cache re-hit check ---")
    first_voice = voices[0]
    first_line = LINES[0]
    audio, mime, style_method = await tts.synthesize(first_line, first_voice)
    timing = tts._last_timings
    print(
        f"re-request voice={first_voice!r} line=0: "
        f"total={timing.get('total_ms', -1):6.0f}ms "
        f"bytes={len(audio):6d} mime={mime} style={style_method} "
        f"(same bytes as before: {audio == (out_dir / f'line00_{first_voice}.pcm').read_bytes()})"
    )

    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
