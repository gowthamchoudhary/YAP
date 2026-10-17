"""Real Jev (TypeSafe / System One) decision test. Usage: python scripts/test_jev.py

Sends one small choice question ("which candidate should speak next, or none")
to the real Jev endpoint and prints probabilities + confidence + latency. Then
it verifies the confidence-fallback path of JevDecider.

Requires JEV_API_KEY in backend/.env (or USE_JEV=false to use only the local heuristics).
"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import decider  # noqa: E402
from config import get, get_bool  # noqa: E402


async def main() -> None:
    if not get_bool("USE_JEV", False):
        print("USE_JEV is false — set USE_JEV=true in backend/.env to enable the JevDecider.")
    if not get("JEV_API_KEY"):
        print("Missing required environment variable: JEV_API_KEY. Add it in backend/.env,")
        print("or run with USE_JEV=false to stay on the local FallbackDecider.")
        sys.exit(1)

    d = decider.JevDecider()
    print(f"endpoint: {d.endpoint} | model: {d.model} | min_conf: {d.min_conf} | timeout: {d.timeout_ms}ms")

    state = {
        "mode_id": "conspiracy",
        "phase": "normal",
        "characters": [
            {"object_id": "obj1", "name": "Lampy", "role": "mastermind", "personality": "delighted schemer",
             "speaks_often": 0.9, "starts_silent": False, "triggered": True, "last_spoke_turn": -99,
             "recent_count": 0, "relation_to_last": ""},
            {"object_id": "obj2", "name": "Stan the Stairs", "role": "overconfident_enforcer", "personality": "loud braggart",
             "speaks_often": 0.8, "starts_silent": False, "triggered": True, "last_spoke_turn": -99,
             "recent_count": 0, "relation_to_last": ""},
        ],
        "played": [{"speaker_id": "obj1", "text": "The plan tonight: one blinding flash and total chaos."}],
        "last_event": {"source": "character", "speaker_id": "obj1", "text": "The plan tonight: one blinding flash and total chaos."},
        "last_user_text": "why are you all staring at me?",
        "turn_index": 1,
    }
    t0 = time.perf_counter()
    decision = await d.choose_speaker(state)
    ms = (time.perf_counter() - t0) * 1000
    jev = decision.debug.get("jev", {})
    print(json.dumps({
        "speaker": decision.speaker_id,
        "reason": decision.reason,
        "jev_used": jev.get("used"),
        "confidence": jev.get("confidence"),
        "probabilities": jev.get("probs"),
        "total_decide_latency_ms": round(ms),
    }, indent=2))
    if not jev.get("used"):
        print("-> Jev answer fell back to FallbackDecider; see fallback_reason in debug above.")


if __name__ == "__main__":
    asyncio.run(main())
