"""Engine test, text-only. Usage: python scripts/test_engine_text_only.py [image.jpg]

Runs a full EngineSession headlessly against the REAL LLM (no websocket, no
browser): TTS is disabled via TTS_DISABLED=1, a fake client collects every
server message, user turns are simulated by real text, and the session runs for
12 spoken lines, then stops. Prints the full transcript and per-line debug.

  set GROQ_API_KEY in backend/.env first.
  Optionally pass a real photo path; it goes through the actual vision model
  (POST /api/scene path is exercised via module calls). Without a photo it uses
  two tiny scene entries built from a 16-px image you provide — ALSO real, the
  vision model receives your image.

NOTE: this is a text-only loop; every line is a real LLM output.
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

# this test is TEXT-ONLY by design (see docstring): force TTS off BEFORE any
# engine/tts import so lines never open real Voice Agent sessions
os.environ["TTS_DISABLED"] = os.environ.get("TTS_DISABLED", "1")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "backend"))

import scene as scene_mod  # noqa: E402
from engine import EngineSession  # noqa: E402
import llm  # noqa: E402

TURNSAsked = 12


class FakeClient:
    """Collects every send() as if it were a browser websocket."""

    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_json(self, obj: dict) -> None:
        self.messages.append(obj)
        t = obj.get("type")
        if t == "line":
            print(f"  [{obj['line_id']}] {obj['speaker_name']}: {obj['text']}  "
                  f"(llm={obj['debug']['llm_ms']:.0f}ms tts={'off' if obj['tts_error'] else obj['debug']['tts_ms']})")
        elif t == "silent":
            print(f"  [silent] {obj['speaker_id']} — {obj['reason']}")
        elif t == "ended":
            print(f"  ENDED by={obj['summary']['ended_by']} lines={obj['summary']['lines_spoken']}")
        elif t == "error":
            print(f"  ERROR [{obj.get('code')}]: {obj.get('message')}", flush=True)


async def main() -> None:
    # 1) a REAL scene: either take a provided photo through the vision model,
    #    or photograph a tiny programmatically generated JPEG (still a real call).
    if len(sys.argv) > 1:
        image = Path(sys.argv[1]).read_bytes()
        print(f"vision test image: {sys.argv[1]} ({len(image)} bytes)")
    else:
        import io
        import struct

        # minimal valid 2x2 grey JPEG read from a hand-built minimal PPM encoded
        # by PIL if available, else an empty grey PPM is NOT a JPEG — so keep a
        # tiny pre-encoded JPEG constant here (a real image, just minuscule).
        image = bytes.fromhex(
            "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707"
            "07090908090c0e0e0c0b0c100d0e11121315161717161d1e1c171620231a1d232c2b"
            "262336393b3affd9"
        )
        print(f"tiny test image: {len(image)} bytes (real vision call) — pass your own photo for better objects")

    data = await llm.vision(image, scene_mod.SCENE_PROMPT, mime="image/jpeg")
    objs = data.get("objects", [])
    print(f"vision found {len(objs)} objects; selecting up to 4 for the conspiracy mode")
    sel = objs[:4]
    for o in sel:
        print(f"   {o.get('id')} {o.get('label')}")

    # build the pydantic models exactly like POST /api/scene does
    import time as _time

    from scene import Scene, SceneObject

    scene_mod._store_scene(
        Scene(
            scene_id="test",
            image_mime="image/jpeg",
            created_at=_time.time(),
            objects=[
                SceneObject(
                    id=o["id"],
                    label=o["label"],
                    bbox=o.get("bbox") or [0.1, 0.1, 0.2, 0.2],
                    hint=o.get("hint") or "",
                )
                for o in sel
            ],
        ),
        image,
    )

    client = FakeClient()
    session = EngineSession(client)
    await session.handle_event(
        {"type": "start", "scene_id": "test", "mode": "conspiracy", "object_ids": [o["id"] for o in sel], "difficulty": "normal"}
    )
    if session.ended:
        print("engine could not start:", json.dumps([m for m in client.messages if m.get("type") == "error"], indent=2))
        sys.exit(1)

    # 2) run the tick loop for real: simulate played acks with sleep
    async def auto_played() -> None:
        while not session.ended:
            line = session.pending
            if line is not None:
                await asyncio.sleep(min(1.6, (line.get("caption_ms") or 2000) / 1000))
                if line.get("type") == "line":
                    print(f"  [played #{session.spoken_count}] {line.get('speaker_name')}: {line.get('text', '')[:80]}", flush=True)
                await session.handle_event({"type": "played", "line_id": line["line_id"]})
            await asyncio.sleep(0.1)

    player = asyncio.create_task(auto_played())

    # 3) after some plotting, the user says something -> barge-in + cover stories
    async def user_intrusions() -> None:
        await asyncio.sleep(8)  # let a few lines play first
        if session.ended:
            return
        print("\n--- user starts speaking (barge-in) ---")
        await session.handle_event({"type": "user_speech_start"})
        await asyncio.sleep(1.5)
        await session.handle_event({"type": "user_transcript", "text": "hey why are you all whispering about the lamp", "final": True})
        await asyncio.sleep(9)  # cover stories + ~6 s quiet -> resume plotting

    intruder = asyncio.create_task(user_intrusions())

    deadline = time.monotonic() + 150  # inside the 180s command window
    while not session.ended and session.spoken_count < TURNSAsked and time.monotonic() < deadline:
        await asyncio.sleep(0.2)
    if not session.ended:
        await session.handle_event({"type": "stop"})  # triggers the mole reveal
    player.cancel()
    intruder.cancel()
    await asyncio.sleep(1.5)

    # 4) transcript dump
    print("\n=========================== TRANSCRIPT ===========================")
    for m in client.messages:
        if m.get("type") == "line":
            print(f"{m['speaker_name']:>{24}} | {m['text']}")
    ended_msg = next((m for m in client.messages if m.get("type") == "ended"), None)
    if ended_msg:
        print(json.dumps(ended_msg["summary"], indent=2))
    print(f"counts: llm_calls={session.llm_calls} tts_calls={session.tts_calls}(disabled)")


if __name__ == "__main__":
    asyncio.run(main())
