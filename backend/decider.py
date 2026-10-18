"""Who speaks next?

One interface (`choose_speaker(state) -> Decision`), one implementation:

- FallbackDecider: pure-local weighted heuristic. Addressed-name mentions win,
  role fit, connection to the last line, novelty, recent-speech penalty, jitter.
  Silence is allowed (returns speaker_id None); below the score threshold nobody
  jumps in, and a directly-addressed start_silent character fires a `silent`
  beat instead of a line.

`state` is documented at _normalize_state below (built by engine.py).
"""
import random
from dataclasses import dataclass, field
from typing import Any, Protocol

# Weights (FallbackDecider)
W_ADDRESSED = 2.2       # user said this character's name/label/role
W_ROLE_FIT = 1.0        # speaks_often prior from the mode
W_CONNECTION = 0.7      # connection to the last line (heuristic, see below)
W_NOVELTY = 0.6         # not spoken recently
W_RECENT_SPEECH = 0.45  # spoke in the last turns
SPEAK_THRESHOLD = 0.85  # below this: None = keep silence
JITTER = 0.12
COOLDOWN_TURNS = 1      # can't speak twice in a row unless only candidate


@dataclass
class Decision:
    speaker_id: str | None
    reason: str
    debug: dict[str, Any] = field(default_factory=dict)


def _normalize_state(state: dict[str, Any]) -> dict[str, Any]:
    return {
        # mode/phase: "normal" | "cover" | "reveal" (see engine.py)
        "mode_id": state.get("mode_id", ""),
        "phase": state.get("phase", "normal"),
        "difficulty": state.get("difficulty", ""),
        # character profiles: {object_id, name, label, role, speaks_often,
        #   starts_silent, triggered, last_spoke_turn, recent_count, relation_to_last}
        "characters": state.get("characters", []),
        # shared history of PLAYED lines: [{speaker_id, text}]
        "played": state.get("played", []),
        # what the user last said (final transcript)
        "last_user_text": state.get("last_user_text", ""),
        # {source: "user"|"character", speaker_id?, text}
        "last_event": state.get("last_event", {}),
        "turn_index": state.get("turn_index", 0),
    }


def _mentions(name: str | None, text: str) -> bool:
    n = (name or "").strip().lower()
    if len(n) < 3:
        return False
    return n in (text or "").lower()


class Decider(Protocol):
    def choose_speaker(self, state: dict[str, Any]) -> Decision: ...


class FallbackDecider:
    """Weighted heuristic pick; may return None (silence is allowed)."""

    def choose_speaker(self, state: dict[str, Any]) -> Decision:
        s = _normalize_state(state)
        chars = s["characters"]
        if not chars:
            return Decision(None, "no characters")
        last = s["last_event"]
        user_text = s["last_user_text"] or (
            last.get("text", "") if last.get("source") == "user" else ""
        )
        turn = s["turn_index"]

        best_id, best_score, best_breakdown = None, -1e9, {}
        addressed_any = False
        for c in chars:
            cid = c["object_id"]
            # starts_silent gate: silent until directly addressed (triggered) or
            # until the engine forces them out (phase == "reveal").
            is_gated = c.get("starts_silent") and not c.get("triggered") and s["phase"] != "reveal"
            is_cooling = (turn - c.get("last_spoke_turn", -99) <= COOLDOWN_TURNS) and len(chars) > 1

            addressed = 1.0 if (
                _mentions(c["name"], user_text)
                or _mentions(c.get("label"), user_text)
                or _mentions(c.get("role"), user_text)
            ) else 0.0
            if addressed:
                addressed_any = True
            if is_gated or (is_cooling and not addressed):
                continue

            role_fit = float(c.get("speaks_often", 0.5))
            connection = 0.0
            if user_text and user_text.rstrip().endswith("?"):
                if c.get("role") == "skeptic":
                    connection += 0.2
                elif c.get("role") == "technical" and len(user_text.split()) > 12:
                    connection += 0.2
            elif (
                last.get("source") == "character"
                and last.get("speaker_id") != cid
                and c.get("relation_to_last")
            ):
                connection = 0.35
            if s["phase"] == "cover":
                connection += 0.4 if c.get("role") in ("overconfident_enforcer", "mastermind") else 0.15

            turns_since = turn - c.get("last_spoke_turn", -99)
            novelty = W_NOVELTY if turns_since >= 3 or turns_since < 0 else 0.25
            recent = float(c.get("recent_count", 0.0))

            score = (
                W_ADDRESSED * addressed
                + W_ROLE_FIT * role_fit
                + W_CONNECTION * connection
                + novelty
                - W_RECENT_SPEECH * recent
                + random.uniform(0, JITTER)
            )
            if score > best_score:
                best_id, best_score, best_breakdown = cid, score, {
                    "addressed": addressed,
                    "role_fit": role_fit,
                    "connection": round(connection, 2),
                    "novelty": novelty,
                    "recent": recent,
                }

        if best_id is None or (best_score < SPEAK_THRESHOLD and not best_breakdown.get("addressed")):
            if addressed_any:
                # someone was addressed but everyone eligible has spoken too recently
                addressed_ids = [
                    c["object_id"]
                    for c in chars
                    if _mentions(c["name"], user_text) or _mentions(c.get("label"), user_text)
                ]
                gated = next(
                    (c["object_id"] for c in chars if c["object_id"] in addressed_ids and c.get("starts_silent") and not c.get("triggered") and s["phase"] != "reveal"),
                    None,
                )
                if gated:
                    return Decision(
                        None,
                        "user addressed a start_silent character; it stays hidden",
                        {"silent_id": gated},
                    )
            return Decision(
                None,
                f"best score {best_score:.2f} below threshold {SPEAK_THRESHOLD:.2f} — silence",
                {"breakdown": best_breakdown, "threshold": SPEAK_THRESHOLD},
            )
        # the user addressed ONLY a still-silent character -> keep the comedy beat:
        # nobody else jumps in; engine emits a silent {speaker_id} event instead.
        addressed_gated = [
            c["object_id"]
            for c in chars
            if c.get("starts_silent")
            and not c.get("triggered")
            and s["phase"] != "reveal"
            and (_mentions(c["name"], user_text) or _mentions(c.get("label"), user_text))
        ]
        if addressed_gated and not best_breakdown.get("addressed"):
            return Decision(
                None,
                "user addressed a start_silent character; it stays hidden",
                {"silent_id": addressed_gated[0]},
            )
        return Decision(
            best_id,
            f"score {best_score:.2f} scored {best_breakdown}",
            {"score": round(best_score, 2), "breakdown": best_breakdown},
        )


def make_decider() -> Decider:
    return FallbackDecider()


if __name__ == "__main__":
    # Offline scenario: run FallbackDecider over 3 scripted turns; no external calls.
    d = FallbackDecider()
    state = {
        "mode_id": "conspiracy",
        "phase": "normal",
        "characters": [
            {"object_id": "obj1", "name": "Lampy", "role": "mastermind", "speaks_often": 0.9, "starts_silent": False, "last_spoke_turn": -99, "recent_count": 0, "relation_to_last": ""},
            {"object_id": "obj2", "name": "Stan the Stairs", "role": "overconfident_enforcer", "speaks_often": 0.8, "starts_silent": False, "last_spoke_turn": -99, "recent_count": 0, "relation_to_last": ""},
            {"object_id": "obj3", "name": "Mo the Mole Mug", "role": "mole", "speaks_often": 0.2, "starts_silent": True, "triggered": False, "last_spoke_turn": -99, "recent_count": 0, "relation_to_last": ""},
        ],
        "played": [],
        "last_event": {},
        "last_user_text": "hey Lampy, what's up?",
        "turn_index": 0,
    }
    for i in range(3):
        dec = d.choose_speaker(state)
        print(f"turn {i}: speaker={dec.speaker_id} reason={dec.reason}")
