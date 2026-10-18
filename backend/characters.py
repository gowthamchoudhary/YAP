"""Character creation: ONE LLM call turns selected objects into voiced characters.

Given the scene objects, the chosen mode (and difficulty for judges), one Groq
call returns name, role, personality, speaking habit, catchphrase and pairwise
relationships per character. Then distinct voices are assigned from voices.json.
"""
import json
import random
import sys
import time
from typing import Any

import llm
import apilog
from config import backend_dir

VOICES_PATH = backend_dir() / "voices.json"

_LEN_CACHE: tuple[float, int] | None = None


def load_voices() -> list[str]:
    """Accepted shapes: {"voices": ["id", {...}, ...]} - objects must have an `id`."""
    if not VOICES_PATH.exists():
        raise RuntimeError(
            f"voices.json not found at {VOICES_PATH}. "
            "Create it (see .env.example sibling README section) with the voice list from the AssemblyAI TTS docs."
        )
    data = json.loads(VOICES_PATH.read_text(encoding="utf-8"))
    raw = data.get("voices") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        raise RuntimeError("voices.json must contain a top-level `voices` list.")
    ids: list[str] = []
    for item in raw:
        if isinstance(item, str):
            ids.append(item)
        elif isinstance(item, dict) and isinstance(item.get("id"), str):
            ids.append(item["id"])
    return ids


def assign_voices(count: int) -> list[str]:
    """Distinct shuffled voices; fails clearly when there are too few."""
    global _LEN_CACHE
    if _LEN_CACHE and _LEN_CACHE[0] > time.time() - 5.0:
        n = _LEN_CACHE[1]
    else:
        n = len(load_voices())
        _LEN_CACHE = (time.time(), n)
    if n < count:
        raise RuntimeError(
            f"voices.json has {n} voice(s) but {count} characters were requested. "
            "Add more voice IDs from the AssemblyAI TTS docs to backend/voices.json "
            "or reduce the number of selected objects."
        )
    chosen = random.sample(load_voices(), count)
    return chosen


def _max_objects_from_mode(mode: dict[str, Any]) -> int:
    try:
        n = len(mode.get("roles", []))
    except Exception:  # noqa: BLE001
        n = 0
    return max(3, min(n, 4))


def _build_prompt(
    objects: list[dict[str, Any]],
    mode: dict[str, Any],
    difficulty: str | None,
) -> str:
    obj_lines = "\n".join(
        f"- id={o['id']} label=\"{o['label']}\" hint: {o.get('hint', '')}" for o in objects
    )
    role_lines = "\n".join(
        f"- {r['role']}: {r['personality']} | speaks_often={r['speaks_often']}"
        f"{' | starts_silent' if r.get('starts_silent') else ''}"
        for r in mode.get("roles", [])
    )
    difficulty_note = (
        f"\nDifficulty for judge tone (calibrate personality to it): {difficulty}"
        if difficulty
        else ""
    )
    return f"""MODE: {mode.get('name')} ({mode.get('id')})
TONE: {mode.get('tone')}
AVAILABLE ROLES (assign each to exactly one character, none Slack):
{role_lines}
{difficulty_note}

OBJECTS THE USER PICKED:
{obj_lines}

Turn EVERY object above into one independent voiced cartoon character.
Requirements:
- Distinct name that fits the object (the object itself can be the name, but give it a nickname flair).
- role MUST be one of the roles listed above, used exactly once each.
- personality: 1-2 sentences.
- speaking_habit: 1 sentence about HOW it talks (not what it says).
- catchphrase: one short spoken line the character would repeat (<=10 words).
- relationships: for every unordered pair of characters, one short line describing how they relate, e.g. "mole secretly blackmails enforcer".
- Every character speaks the SAME language as the user is speaking: plain English unless the user's spoken words are in another language.

Reply ONLY with JSON, exact shape:
{{
  "characters": [
    {{"object_id": "<object id above>", "name": "...", "role": "<role>", "personality": "...", "speaking_habit": "...", "catchphrase": "..."}}
  ],
  "relationships": [
    {{"a": "<object_id>", "b": "<object_id>", "relation": "..."}}
  ]
}}"""


def _validate(chars_raw: dict[str, Any], objects: list[dict[str, Any]], mode: dict[str, Any]) -> dict[str, Any]:
    obj_ids = [o["id"] for o in objects]
    valid_roles = {r["role"] for r in mode.get("roles", [])}
    chars = chars_raw.get("characters")
    if not isinstance(chars, list):
        raise ValueError("missing 'characters' list")
    seen_ids: set[str] = set()
    cleaned: list[dict[str, Any]] = []
    for c in chars:
        if not isinstance(c, dict):
            continue
        oid = c.get("object_id")
        if oid not in obj_ids or oid in seen_ids:
            continue
        seen_ids.add(oid)
        role = c.get("role")
        cleaned.append(
            {
                "object_id": oid,
                "label": next(o["label"] for o in objects if o["id"] == oid),
                "name": str(c.get("name") or c.get("label") or oid)[:32],
                "role": role if role in valid_roles else None,
                "personality": str(c.get("personality") or "")[:400],
                "speaking_habit": str(c.get("speaking_habit") or "")[:250],
                "catchphrase": str(c.get("catchphrase") or "")[:80],
            }
        )
    # Every picked object must end up as a character; any that didn't survive
    # gets a role only as error handling (not mock data), then we re-check count.
    missing = [oid for oid in obj_ids if oid not in seen_ids]
    if missing:
        raise ValueError(f"LLM did not produce characters for objects: {missing}")
    rels_raw = chars_raw.get("relationships", [])
    rels = []
    ids_set = set(obj_ids)
    if isinstance(rels_raw, list):
        for r in rels_raw:
            if (
                isinstance(r, dict)
                and r.get("a") in ids_set
                and r.get("b") in ids_set
                and r.get("a") != r.get("b")
            ):
                rels.append({"a": r["a"], "b": r["b"], "relation": str(r.get("relation", ""))[:160]})
    return {"characters": cleaned, "relationships": rels}


async def build_characters(
    objects: list[dict[str, Any]], mode: dict[str, Any], difficulty: str | None = None
) -> dict[str, Any]:
    """objects: scene objects chosen by user; mode: loaded mode dict."""
    count = len(objects)
    if count < 1:
        raise RuntimeError("No objects selected; nothing to turn into characters.")

    prompt = _build_prompt(objects, mode, difficulty)
    t0 = time.perf_counter()
    raw_text = await llm.chat(
        [prompt],
        max_tokens=1200,
        temperature=0.85,
        response_format={"type": "json_object"},
    )
    latency = (time.perf_counter() - t0) * 1000
    apilog.log_call(
        service="characters",
        endpoint="groq.text[characters]",
        status="ok",
        latency_ms=latency,
    )

    last_err: Exception | None = None
    for attempt in range(2):
        try:
            data = llm._parse_json(raw_text)
            result = _validate(data, objects, mode)
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt == 0:
                # one retry: stricter reminder, tiny temperature to stay deterministic
                prompt += "\n\nREMINDER: return ONLY that exact JSON shape, one entry per object id above. No extra text."
                raw_text = await llm.chat(
                    [prompt], max_tokens=1200, temperature=0.3, response_format={"type": "json_object"}
                )
            else:
                raise RuntimeError(
                    f"Character LLM call did not return valid characters JSON: {last_err}"
                ) from last_err

    # role fallback (only when the model invented an invalid role label)
    roles = [r["role"] for r in mode.get("roles", [])]
    used = [c["role"] for c in result["characters"] if c["role"]]
    free_roles = [r for r in roles if r not in used]
    for c in result["characters"]:
        if not c["role"]:
            if not free_roles:
                raise RuntimeError(
                    f"No role available for '{c['name']}' and mode has no free roles left."
                )
            c["role"] = free_roles.pop(0)

    voices = assign_voices(len(result["characters"]))
    for c, voice in zip(result["characters"], voices):
        c["voice_id"] = voice

    print(
        f"[characters] built {len(result['characters'])} characters in {latency:.0f}ms: "
        + ", ".join(f"{c['name']}({c['role']}|{c['voice_id']})" for c in result["characters"]),
        file=sys.stderr,
    )
    return result


if __name__ == "__main__":
    import asyncio

    async def _main() -> None:
        demo_objects = [{"id": "obj1", "label": "test lamp", "hint": "scheming"}, {"id": "obj2", "label": "mug", "hint": "nervous"}]
        demo_mode = json.loads((backend_dir() / "modes" / "conspiracy.json").read_text(encoding="utf-8"))
        out = await build_characters(demo_objects, demo_mode, None)
        print(json.dumps(out, indent=2, ensure_ascii=False))

    asyncio.run(_main())
