"""Offline decider test. Usage: python scripts/test_decider.py

No external API: runs the FallbackDecider over a scripted conspiracy scenario,
printing the chosen speaker, score breakdown, and the silence/silent-gate paths.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from decider import FallbackDecider, SPEAK_THRESHOLD  # noqa: E402


def char(cid, name, role, speaks_often, starts_silent=False, triggered=False):
    return {
        "object_id": cid,
        "name": name,
        "label": name.split()[0].lower(),
        "role": role,
        "speaks_often": speaks_often,
        "starts_silent": starts_silent,
        "triggered": triggered,
        "last_spoke_turn": -99,
        "recent_count": 0,
        "relation_to_last": "",
    }


def state(chars, played, user_text, turn, phase="normal"):
    return {
        "mode_id": "conspiracy",
        "phase": phase,
        "characters": chars,
        "played": played,
        "last_event": {"source": "user", "text": user_text} if user_text else None,
        "last_user_text": user_text,
        "turn_index": turn,
    }


def main() -> None:
    d = FallbackDecider()
    chars = [
        char("obj1", "Lampy", "mastermind", 0.9),
        char("obj2", "Stan the Stairs", "overconfident_enforcer", 0.8),
        char("obj3", "Mo the Mole Mug", "mole", 0.2, starts_silent=True),
    ]
    st = state(chars, [], "hey Lampy, what are you all whispering about?", 0)

    for i in range(6):
        dec = d.choose_speaker(st)
        who = dec.speaker_id or "(silence)"
        print(f"turn {i}: -> {who:6} | {dec.reason}")
        if dec.debug.get("breakdown"):
            print(f"         breakdown: {dec.debug['breakdown']} (threshold {SPEAK_THRESHOLD})")
        if dec.speaker_id:
            line = f"{chars[[c['object_id'] for c in chars].index(dec.speaker_id)]['name']} says something."
            st["played"].append({"speaker_id": dec.speaker_id, "text": line})
            st["turn_index"] += 1
            c = chars[[c["object_id"] for c in chars].index(dec.speaker_id)]
            c["last_spoke_turn"] = st["turn_index"]
            c["recent_count"] = min(2.0, c["recent_count"] + 1.0)
            st["last_event"] = {"source": "character", "speaker_id": dec.speaker_id, "text": line}
        if dec.debug.get("silent_id"):
            print(f"         silent event would fire for {dec.debug['silent_id']}")

    # --------- addressed-but-silent gate
    print("\n-- user addresses the mole directly:")
    chars[2]["triggered"] = False
    dec = d.choose_speaker(state(chars, st["played"], "hey Mo the Mole Mug, are you in on this?", st["turn_index"]))
    print(f"   -> {dec.speaker_id or '(silence)'} | {dec.reason} | silent_id={dec.debug.get('silent_id')}")
    chars[2]["triggered"] = True
    dec = d.choose_speaker(state(chars, st["played"], "hey Mo the Mole Mug, are you in on this?", st["turn_index"]))
    print(f"   after trigger -> {dec.speaker_id or '(silence)'} | {dec.reason}")


if __name__ == "__main__":
    main()
