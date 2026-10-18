"""The turn loop.

One EngineSession per /ws/session connection; one asyncio engine task per
session. Event -> shared history -> decider picks speaker -> LLM line -> TTS ->
`line` to client -> wait for `played` ack -> publish that line as a new event.

Rules implemented here (per product spec):
  - only PLAYED lines enter shared history (a cut-off line is discarded),
  - each generation carries a snapshot_id; a line gone stale is discarded,
  - at most 1 generation in flight (a lock),
  - the next line is prefetched while the current one plays,
  - lines: max 18 words, one sentence, react to the previous line,
    no stage directions/emojis/quotes; anti-repetition against the last 8,
  - hard limits: 40 lines / 10 minutes per session; Stop works instantly,
  - user_speech_start: cancel in-flight generation, drop queued lines,
    trigger the mode's interruption behaviour (conspiracy cover stories),
  - conspiracy ending: ~14 spoken lines or Stop -> the mole confesses,
  - judges: pause-aware questions, Done or 3 min -> per-judge score + feedback,
  - lines carry a mood tag -> a `delivery` (whisper|normal|shout|nervous|sarcastic|
    slow|fast) and one whitelisted emoji (stripped from text before TTS),
  - user_reaction -> a character responds in words + emoji,
  - revoice -> replays the last played line with a DIFFERENT voice (replay:true,
    never enters history),
  - language -> regenerate lines in another language (only if the TTS API
    supports it per backend/tts_capabilities.json).

The client object only needs an async `send_json(dict)` (FastAPI WebSocket has
one; scripts/test_engine_text_only.py collects into a list instead).
"""
import asyncio
import json
import random
import sys
import time
import uuid
from typing import Any

import characters as characters_mod
import decider as decider_mod
import llm
import scene as scene_mod
import tts
from config import backend_dir, require

LINE_CAP = 40
SESSION_TTL_S = 600
JUDGES_QUESTION_TTL_S = 180
JUDGE_REPLY_QUIET_S = 2.2
COVER_DISPATCH_QUIET_S = 1.2
RESUME_PLOTTING_QUIET_S = 6.0
COVER_LINES_MAX = 2

# reaction + line-face emoji whitelist (engine never invents others)
EMOJI_WHITELIST = ["😈", "😏", "😳", "😱", "🤔", "😅", "🙄", "😴", "👀", "🔥", "💡", "🎤", "❓", "✅", "❌", "👏"]
MOODS = ["whisper", "nervous", "smug", "angry", "calm", "excited", "sarcastic"]
DELIVERY_BY_MOOD = {
    "whisper": "whisper",
    "nervous": "nervous",
    "smug": "sarcastic",
    "angry": "shout",
    "calm": "slow",
    "excited": "fast",
    "sarcastic": "sarcastic",
}

MODES_DIR = backend_dir() / "modes"
# in-memory audio registry: line_id -> (audio_bytes, mime_type)
_AUDIO: dict[str, tuple[bytes, str]] = {}


def load_modes() -> list[dict[str, Any]]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(MODES_DIR.glob("*.json"))]


def get_mode(mode_id: str) -> dict[str, Any]:
    for m in load_modes():
        if m.get("id") == mode_id:
            return m
    raise RuntimeError(f"Unknown mode '{mode_id}'. Available: {[m.get('id') for m in load_modes()]}")


def get_audio(line_id: str) -> tuple[bytes, str] | None:
    return _AUDIO.get(line_id)


def _words(s: str) -> list[str]:
    return [w for w in s.split() if w]


def _strip_whitelist_emoji(text: str) -> str:
    return "".join(ch for ch in text if ch not in EMOJI_WHITELIST)


def _clean_text(text: str) -> str:
    t = (text or "").strip()
    while len(t) > 1 and t[0] == t[-1] and t[0] in "\"'\u201c\u201d''":
        t = t[1:-1].strip()
    t = t.replace("*", "").replace("#", "")
    if t and t[-1] not in ".!?":
        t = t.rstrip(",;: -") + "."
    return t


def _token_set(t: str) -> set[str]:
    return {w.lower().strip(".,!?\"'") for w in _words(t) if w.strip(".,!?\"'")}


class _Stale(Exception):
    """Raised when a generation finishes but the conversation moved past it."""


class EngineSession:
    def __init__(self, client: Any) -> None:
        self.client = client
        self.session_id = uuid.uuid4().hex[:12]
        self.decider = decider_mod.make_decider()
        # ---- conversation state
        self.mode: dict[str, Any] | None = None
        self.difficulty: str = "normal"
        self.characters: list[dict[str, Any]] = []
        self.played: list[dict[str, Any]] = []      # shared history (PLAYED only)
        self.user_presentation: list[str] = []      # judges: final user words
        self.last_user_text: str = ""
        self.last_event: dict[str, Any] = {}
        self.history_version = 0
        self.turn = 0
        self.spoken_count = 0
        self.line_counter = 0
        self.started_at = time.time()
        # ---- generation state
        self.gen_lock = asyncio.Lock()
        self.gen_task: asyncio.Task | None = None
        self.pending: dict[str, Any] | None = None  # sent, awaiting `played`
        self.staged: dict[str, Any] | None = None   # prefetched, not yet sent
        # ---- flow control
        self.phase = "idle"  # idle -> normal -> (cover / reveal / score) -> ended
        self.ended_by: str | None = None
        self.paused = False
        self.ended = False
        self.user_is_speaking = False
        self.user_last_final_at: float | None = None
        self.cover_lines_left = 0
        self.language: str | None = None            # set by {type:'language'} if TTS supports it
        self.reaction_context: dict[str, Any] | None = None
        self.last_played_meta: dict[str, Any] | None = None
        self.last_gen_error_at: float | None = None  # backoff after a failed generation
        self.tasks: list[asyncio.Task] = []
        self.relationships: list[dict[str, Any]] = []
        # ---- metrics
        self.llm_calls = 0
        self.tts_calls = 0
        self.decide_ms = 0.0
        self.first_audio_ms: float | None = None

    # ------------------------------------------------------------------ helpers
    def _char_by_id(self, cid: str) -> dict[str, Any] | None:
        return next((c for c in self.characters if c["object_id"] == cid), None)

    async def send(self, obj: dict[str, Any]) -> None:
        try:
            await self.client.send_json(obj)
        except Exception:  # noqa: BLE001 - client gone; engine drains quietly
            self.ended = True

    # ------------------------------------------------------------------ start
    async def handle_start(self, data: dict[str, Any]) -> None:
        try:
            require("GROQ_API_KEY", "Needed for the scene + character + dialogue LLMs.")
            sid = str(data.get("scene_id") or "")
            entry = scene_mod.get_scene(sid)
            if not entry:
                raise RuntimeError("Unknown scene_id: retake or upload the photo on the Capture screen.")
            want = {str(o) for o in (data.get("object_ids") or [])}
            objects = [o.model_dump() for o in entry["scene"].objects if o.id in want]
            if not objects:
                raise RuntimeError("None of the object ids matched this scene. Go back and re-select.")
            self.mode = get_mode(str(data.get("mode") or ""))
            if len(objects) > self.mode.get("cap", 4):
                objects = objects[: self.mode.get("cap", 4)]
            self.difficulty = str(data.get("difficulty") or "normal")
            # per-character Setup-screen edits: {object_id: {name, traits, voice_id}}
            edits = data.get("character_edits") if isinstance(data.get("character_edits"), dict) else None

            chars_out = await characters_mod.build_characters(objects, self.mode, self.difficulty, edits=edits)
            roles = {r["role"]: r for r in self.mode.get("roles", [])}
            self.relationships = chars_out.get("relationships", [])
            self.characters = [
                {
                    **c,
                    "speaks_often": float(roles.get(c["role"], {}).get("speaks_often", 0.5)),
                    "starts_silent": bool(roles.get(c["role"], {}).get("starts_silent", False)),
                    "triggered": False,
                    "last_spoke_turn": -99,
                    "recent_count": 0,
                    "relation_to_last": "",
                }
                for c in chars_out["characters"]
            ]
            self.phase = "normal"
            await self.send(
                {
                    "type": "characters",
                    "session_id": self.session_id,
                    "mode": {
                        "id": self.mode["id"],
                        "name": self.mode["name"],
                        "cap": self.mode.get("cap"),
                        "accent": self.mode.get("accent", ""),
                    },
                    "characters": [
                        {
                            "object_id": c["object_id"],
                            "name": c["name"],
                            "label": c["label"],
                            "role": c["role"],
                            "voice_id": c["voice_id"],
                            "catchphrase": c["catchphrase"],
                            "personality": c["personality"],
                            "traits": (edits or {}).get(c["object_id"], {}).get("traits", []),
                            "speaking_habit": c.get("speaking_habit", ""),
                            "starts_silent": c["starts_silent"],
                        }
                        for c in self.characters
                    ],
                }
            )
            self.tasks.append(asyncio.create_task(self._tick_loop()))
        except Exception as exc:  # noqa: BLE001 - startup failure goes to the user
            await self.send({"type": "error", "code": "start_failed", "message": str(exc)})
            self.ended = True

    # ------------------------------------------------------------------ client events
    async def handle_event(self, data: dict[str, Any]) -> None:
        etype = data.get("type")
        try:
            if etype == "start":
                await self.handle_start(data)
            elif etype == "user_speech_start":
                await self._on_user_speech_start()
            elif etype == "user_transcript":
                self._on_user_transcript(data)
            elif etype == "played":
                self._on_played(data)
            elif etype == "user_reaction":
                await self._on_user_reaction(data)
            elif etype == "revoice":
                await self._on_revoice()
            elif etype == "language":
                await self._on_language(data)
            elif etype == "pause":
                self.paused = True
                await self.send({"type": "status", "status": "paused"})
            elif etype == "resume":
                self.paused = False
                await self.send({"type": "status", "status": self.phase})
            elif etype == "stop":
                await self._on_stop()
            elif etype == "done":
                await self._on_done()
        except Exception as exc:  # noqa: BLE001
            await self.send({"type": "error", "code": "engine", "message": str(exc)})

    async def _on_user_speech_start(self) -> None:
        self.user_is_speaking = True
        self.user_last_final_at = None
        if self.gen_task and not self.gen_task.done():
            self.gen_task.cancel()  # cancel in-flight generation
            self.gen_task = None
        self.staged = None   # discard queued lines
        self.pending = None  # a cut line never becomes played history
        if (self.mode or {}).get("id") == "conspiracy" and self.phase == "normal":
            self.phase = "cover"
            self.cover_lines_left = COVER_LINES_MAX
        await self.send({"type": "status", "status": "listening"})

    def _on_user_transcript(self, data: dict[str, Any]) -> None:
        text = str(data.get("text") or "").strip()
        if not text:
            return
        if not data.get("final"):
            return
        self.user_is_speaking = False
        self.user_last_final_at = time.time()
        self.user_presentation.append(text)
        self.last_user_text = text
        self.last_event = {"source": "user", "text": text}
        self.reaction_context = None  # words beat a pending emoji reaction
        # directly addressing a start_silent character triggers them
        for c in self.characters:
            if c.get("starts_silent") and not c.get("triggered"):
                if decider_mod._mentions(c["name"], text) or decider_mod._mentions(c.get("label"), text):
                    c["triggered"] = True

    def _on_played(self, data: dict[str, Any]) -> None:
        line = self.pending
        if not line or data.get("line_id") != line["line_id"]:
            return
        self.pending = None
        self.played.append({"speaker_id": line["speaker_id"], "text": line["text"]})
        self.last_played_meta = {
            "speaker_id": line["speaker_id"],
            "text": line["text"],
            "delivery": line.get("delivery"),
        }
        self.history_version += 1
        self.spoken_count += 1
        self.turn += 1
        c = self._char_by_id(line["speaker_id"])
        if c:
            c["last_spoke_turn"] = self.turn
            c["recent_count"] = min(2.0, c["recent_count"] + 1.0)
        for other in self.characters:
            if other is not c:
                other["recent_count"] = max(0.0, other["recent_count"] - 0.34)
        self.last_event = {"source": "character", "speaker_id": line["speaker_id"], "text": line["text"]}
        # promote a still-valid prefetch
        staged, self.staged = self.staged, None
        if staged and staged.get("snapshot") == self.history_version and not self.ended:
            self.pending = staged
            asyncio.get_event_loop().create_task(self.send(staged))
            self._schedule_prefetch()

    # ------------------------------------------------------------------ reaction / revoice / language
    async def _on_user_reaction(self, data: dict[str, Any]) -> None:
        emoji = str(data.get("emoji") or "").strip()
        if emoji not in EMOJI_WHITELIST:
            await self.send(
                {
                    "type": "error",
                    "code": "reaction",
                    "message": f"Emoji not in the reaction whitelist: {emoji!r}",
                }
            )
            return
        raw_target = data.get("target_id")
        target = self._char_by_id(str(raw_target)) if raw_target else None
        if target:
            target["triggered"] = True  # directly reacting AT a silent character unlocks them
        self.last_user_text = ""  # a reaction carries no words
        self.last_event = {"source": "user", "text": "", "reaction": emoji}
        self.user_last_final_at = time.time()
        if self.phase in ("normal", "cover"):
            self.reaction_context = {
                "emoji": emoji,
                "target_id": target["object_id"] if target else None,
            }
        await self.send({"type": "status", "status": f"reacted {emoji}"})

    async def _on_language(self, data: dict[str, Any]) -> None:
        caps = tts.load_capabilities()
        code = str(data.get("code") or "")
        if code and caps.get("languages_supported") and code in caps.get("languages", []):
            self.language = code
            await self.send({"type": "status", "status": f"language: {code}"})
        else:
            await self.send(
                {
                    "type": "error",
                    "code": "language",
                    "message": (
                        f"Language {code!r} is not supported by the TTS API. "
                        "Set languages_supported + languages in backend/tts_capabilities.json first."
                    ),
                }
            )

    async def _on_revoice(self) -> None:
        """Replay the last played line through a DIFFERENT voice (replay:true;
        never enters played history and the client does not ack it back)."""
        meta = self.last_played_meta
        char = self._char_by_id(meta["speaker_id"]) if meta else None
        if not meta or char is None or not char.get("voice_id"):
            await self.send(
                {
                    "type": "error",
                    "code": "revoice",
                    "message": "Nothing has played yet, so there is no last line to re-voice.",
                }
            )
            return
        others = [v for v in characters_mod.load_voices() if v != char["voice_id"]]
        if not others:
            await self.send(
                {
                    "type": "error",
                    "code": "revoice",
                    "message": "voices.json has only one voice, so there is nothing else to re-voice with.",
                }
            )
            return
        new_voice = random.choice(others)
        t0 = time.perf_counter()
        try:
            audio, mime, style_method = await tts.synthesize(
                meta["text"], new_voice, {"delivery": meta.get("delivery"), "language": self.language}
            )
        except tts.TTSNotConfigured as exc:
            await self.send({"type": "error", "code": "revoice", "message": str(exc)})
            return
        except Exception as exc:  # noqa: BLE001
            await self.send({"type": "error", "code": "revoice", "message": str(exc)})
            return
        self.line_counter += 1
        line_id = f"rv{self.line_counter}"
        _AUDIO[line_id] = (audio, mime)
        await self.send(
            {
                "type": "line",
                "line_id": line_id,
                "speaker_id": char["object_id"],
                "speaker_name": char["name"],
                "text": meta["text"],
                "audio_url": f"/api/audio/{line_id}",
                "caption_ms": max(1500, len(_words(meta["text"])) * 260 + 400),
                "replay": True,
                "revoice_from": char["voice_id"],
                "voice_id": new_voice,
                "emoji": None,
                "mood": None,
                "delivery": meta.get("delivery"),
                "style": {"delivery": meta.get("delivery"), "method": style_method},
                "debug": {
                    "llm_ms": 0.0,
                    "tts_ms": round((time.perf_counter() - t0) * 1000, 1),
                    "decider": {"chosen": char["object_id"], "scores": {}, "reason": "re-voice"},
                    "style": {"delivery": meta.get("delivery"), "method": style_method},
                },
                "tts_error": None,
            }
        )

    def _engine_state(self) -> dict[str, Any]:
        return {
            "mode_id": (self.mode or {}).get("id", ""),
            "phase": self.phase,
            "difficulty": self.difficulty,
            "characters": self.characters,
            "played": self.played,
            "last_user_text": self.last_user_text,
            "last_event": self.last_event,
            "turn_index": self.turn,
        }

    # ------------------------------------------------------------------ loops
    async def _tick_loop(self) -> None:
        try:
            while not self.ended:
                await asyncio.sleep(0.3)
                if self.paused or self.ended:
                    continue
                self._check_hard_limits()
                if not self.ended:
                    await self._tick_once()
            await self._finish_if_needed()
        except asyncio.CancelledError:
            raise

    def _check_hard_limits(self) -> None:
        elapsed = time.time() - self.started_at
        mode_id = (self.mode or {}).get("id")
        if mode_id == "judges" and elapsed >= JUDGES_QUESTION_TTL_S and self.phase == "normal":
            asyncio.get_event_loop().create_task(self._on_done())
        elif elapsed >= SESSION_TTL_S:
            asyncio.get_event_loop().create_task(self._finish_if_needed(timeout=True))
        elif (self.mode or {}).get("id") == "conspiracy" and self.spoken_count >= 14 and self.phase == "normal":
            asyncio.get_event_loop().create_task(self._on_stop(auto=True))
        elif self.spoken_count >= LINE_CAP:
            asyncio.get_event_loop().create_task(
                self._on_done() if mode_id == "judges" else self._on_stop(auto=True)
            )

    async def _tick_once(self) -> None:
        """No LLM generation happens here — decisions to generate are raised here.

        At most 1 generation in flight; the prefetch task lives in self.gen_task.
        """
        if self.pending is not None or (self.gen_task and not self.gen_task.done()):
            return
        # after a failed generation (e.g. provider 429) back off before retrying,
        # so a dead provider can never be hammered once per 300 ms tick
        if self.last_gen_error_at is not None and time.time() - self.last_gen_error_at < 5.0:
            return
        mode_id = (self.mode or {}).get("id")
        quiet_for = time.time() - self.user_last_final_at if self.user_last_final_at is not None else None

        if (
            self.reaction_context
            and not self.user_is_speaking
            and self.phase in ("normal", "cover")
        ):
            rc, self.reaction_context = self.reaction_context, None
            target = self._char_by_id(rc["target_id"]) if rc.get("target_id") else None
            if target:
                hint = (
                    f'The user just reacted with {rc["emoji"]} AT {target["name"]}. '
                    f'{target["name"]} answers the reaction in ONE short line (words + emoji face).'
                )
            else:
                hint = (
                    f'The user just reacted with the {rc["emoji"]} emoji (no words). '
                    f'Whoever fits best comments on that reaction in ONE short line (words + emoji face).'
                )
            self.gen_task = asyncio.create_task(self._produce_and_send(rc.get("target_id"), hint))
            return

        if mode_id == "judges" and self.phase == "normal":
            # judges wait for an end-of-turn pause, then ONE pointed question
            if (
                quiet_for is not None
                and quiet_for >= JUDGE_REPLY_QUIET_S
                and not self.staged
                and self.last_event.get("source") == "user"
            ):
                self.gen_task = asyncio.create_task(
                    self._produce_and_send(None, "Ask ONE short pointed question about what the user just presented.")
                )
            return

        if mode_id == "conspiracy" and self.phase == "cover":
            # 1-2 innocent cover lines right after the user quiets down,
            # back to plotting only ~6 s after the user's last words
            if (
                quiet_for is not None
                and quiet_for >= COVER_DISPATCH_QUIET_S
                and self.cover_lines_left > 0
                and self.last_event.get("source") == "user"
            ):
                self.cover_lines_left -= 1
                self.gen_task = asyncio.create_task(
                    self._produce_and_send(
                        None, "The user may be listening. Give your fake-innocent cover line, harmless and sweet."
                    )
                )
            elif (
                quiet_for is not None
                and quiet_for >= RESUME_PLOTTING_QUIET_S
                and self.cover_lines_left <= 0
            ):
                self.phase = "normal"
                await self.send({"type": "status", "status": "back to plotting"})
            return

        if mode_id == "conspiracy" and self.phase == "normal" and not self.user_is_speaking:
            # the opener: nobody has spoken yet
            if not self.played and not self.staged:
                self.gen_task = asyncio.create_task(self._produce_and_send(None, None))
            return

    # ------------------------------------------------------------------ line production
    async def _produce_and_send(self, forced_speaker: str | None, context_hint: str | None) -> None:
        async with self.gen_lock:
            try:
                line = await self._produce_line(forced_speaker, context_hint)
            except _Stale:
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.last_gen_error_at = time.time()
                await self.send({"type": "error", "code": "generation", "message": str(exc)})
                return
            if line is None:
                return
            await self._send_line(line)

    async def _produce_line(
        self, forced_speaker: str | None, context_hint: str | None, max_words_override: int | None = None
    ) -> dict[str, Any] | None:
        """Decide -> LLM -> (anti-rep retry) -> staleness gate -> TTS -> line dict.

        Returns None when the decider calls for silence.
        """
        snapshot = self.history_version + (1 if self.pending else 0)
        t0 = time.perf_counter()
        speaker_id = forced_speaker
        silent_id: str | None = None
        sel_debug: dict[str, Any] = {"chosen": speaker_id, "scores": {}, "reason": "forced"}
        if speaker_id is None:
            dec = self.decider.choose_speaker(self._engine_state())
            if asyncio.iscoroutine(dec):
                dec = await dec
            self.decide_ms = (time.perf_counter() - t0) * 1000
            sel_debug = {
                "chosen": dec.speaker_id,
                "scores": (dec.debug or {}).get("breakdown", {}),
                "reason": dec.reason,
            }
            silent_id = (dec.debug or {}).get("silent_id")
            speaker_id = dec.speaker_id
            if speaker_id is None:
                if silent_id and self.phase != "reveal":
                    await self.send(
                        {
                            "type": "silent",
                            "speaker_id": silent_id,
                            "reason": "stays silent until addressed directly or the reveal",
                        }
                    )
                    self.user_last_final_at = None  # don't loop on the same trigger
                return None
        speaker = self._char_by_id(speaker_id)
        if not speaker:
            return None

        max_words = max_words_override or 18
        prompt_msgs = self._build_prompt(speaker, context_hint, max_words)
        t_llm = time.perf_counter()
        self.llm_calls += 1
        obj = await self._generate_line_obj(prompt_msgs, temperature=0.9)
        llm_ms = (time.perf_counter() - t_llm) * 1000
        mood, emoji = self._pick_mood_emoji(obj)
        line = self._sanitize_line(str(obj.get("text") or ""), speaker, max_words)
        if _too_similar(line, self.played):
            self.llm_calls += 1  # ONE regeneration against repetition
            retry_obj = await self._generate_line_obj(
                prompt_msgs
                + [
                    {
                        "role": "user",
                        "content": "Different wording, same intent. Nothing like the previous lines.",
                    }
                ],
                temperature=1.0,
            )
            alt = self._sanitize_line(str(retry_obj.get("text") or ""), speaker, max_words)
            if not _too_similar(alt, self.played):
                line = alt
                mood, emoji = self._pick_mood_emoji(retry_obj)

        if snapshot != self.history_version:
            raise _Stale()  # went stale: discarded, never played, never in history

        # ---- TTS
        self.line_counter += 1
        line_id = f"l{self.line_counter}"
        t_tts = time.perf_counter()
        audio_url: str | None = None
        tts_error: str | None = None
        self.tts_calls += 1
        delivery = DELIVERY_BY_MOOD.get(mood, "normal")
        try:
            audio, mime, style_method = await tts.synthesize(
                line,
                speaker["voice_id"],
                {"delivery": delivery, "language": self.language},
            )
            _AUDIO[line_id] = (audio, mime)
            audio_url = f"/api/audio/{line_id}"
            if self.first_audio_ms is None:
                self.first_audio_ms = (time.perf_counter() - self.started_at) * 1000
        except tts.TTSNotConfigured as exc:
            tts_error = str(exc)
            style_method = "none"
        tts_ms = (time.perf_counter() - t_tts) * 1000

        return {
            "type": "line",
            "line_id": line_id,
            "speaker_id": speaker_id,
            "speaker_name": speaker["name"],
            "text": line,
            "audio_url": audio_url,
            "caption_ms": max(1500, len(_words(line)) * 260 + 400),
            "snapshot": snapshot,
            "mood": mood,
            "emoji": emoji,
            "delivery": delivery,
            "voice_id": speaker["voice_id"],
            "style": {"delivery": delivery, "method": style_method},
            "debug": {
                "decide_ms": round(self.decide_ms, 1),
                "llm_ms": round(llm_ms, 1),
                "tts_ms": round(tts_ms, 1),
                "first_audio_ms": round(self.first_audio_ms or 0.0, 1),
                "llm_calls": self.llm_calls,
                "tts_calls": self.tts_calls,
                "decider": sel_debug,
                "style": {"delivery": delivery, "method": style_method},
            },
            "tts_error": tts_error,
        }

    async def _generate_line_obj(
        self, prompt_msgs: list[dict[str, str]], temperature: float
    ) -> dict[str, Any]:
        """ONE LLM call -> {"text", "mood", "emoji"} (tolerant parse)."""
        text_raw = await llm.chat(
            prompt_msgs,
            max_tokens=140,
            temperature=temperature,
            response_format={"type": "json_object"},
        )
        try:
            obj = llm._parse_json(text_raw)
        except Exception:  # noqa: BLE001 - prose fallback: use the raw line text
            obj = {"text": text_raw}
        if not isinstance(obj, dict):
            obj = {"text": text_raw}
        return obj

    def _pick_mood_emoji(self, obj: dict[str, Any]) -> tuple[str, str | None]:
        mood = str(obj.get("mood") or "").strip().lower()
        if mood not in MOODS:
            mood = "calm"
        emoji = obj.get("emoji")
        return mood, (emoji if isinstance(emoji, str) and emoji in EMOJI_WHITELIST else None)

    def _sanitize_line(self, raw: str, speaker: dict[str, Any], max_words: int) -> str:
        t = _clean_text(_strip_whitelist_emoji(raw))
        toks = _words(t)
        if len(toks) > max_words:
            first = t.split(". ")[0].rstrip(",;:")
            t = first if len(_words(first)) <= max_words else " ".join(toks[:max_words])
            t = _clean_text(t)
        if not t:
            return f"{speaker['name']} says nothing."
        return t[0].upper() + t[1:]

    def _build_prompt(
        self,
        speaker: dict[str, Any],
        context_hint: str | None,
        max_words: int,
        require_json: bool = True,
    ) -> list[dict[str, str]]:
        mode = self.mode or {}
        system = (
            f"You ARE {speaker['name']}, the {speaker.get('role')}: an ordinary object from the user's photo, "
            "reanimated as an independent cartoon character.\n"
            f"Your object: {speaker.get('label')}. Personality: {speaker.get('personality')}\n"
            f"Speaking habit: {speaker.get('speaking_habit')}\n"
            f"Signature catchphrase (use rarely): \"{speaker.get('catchphrase')}\"\n"
            f"VOICES IN THIS SCENE: {', '.join(c['name'] + ' (' + c['role'] + ')' for c in self.characters)}.\n"
            "Relationships between you all:\n"
            + ("\n".join(f"- {c['name']} & {c['name2']}: {c['relation']}" for c in [
                {"name": self._char_by_id(r["a"]).get("name", r["a"]),
                 "name2": self._char_by_id(r["b"]).get("name", r["b"]),
                 "relation": r["relation"]}
                for r in self.relationships
            ]) or "(none given)")
        )
        history = "\n".join(
            f"{h['speaker_id']}: {h['text']}" for h in self.played[-6:]
        )
        user_ctx = (
            f"Recent spoken lines (you only know what was actually played):\n{history or '(nothing spoken yet)'}\n"
            f"User's last words: \"{self.last_user_text or '(they have said nothing yet)'}\"\n"
            f"Hard format rules: at most {max_words} words, ONE sentence, must react to the previous line. "
            "No stage directions, no emojis in the text itself, no quotes around speech, no narration, no parentheses. "
            "Speak plain English unless the user clearly speaks another language."
        )
        if require_json:
            user_ctx += (
                "\n\nThen pick: your mood this line, one of [whisper, nervous, smug, angry, calm, excited, sarcastic], "
                "and ONE face emoji that matches it, from this list only: "
                + " ".join(EMOJI_WHITELIST)
                + '.\nReply ONLY with JSON: {"text": "<your line>", "mood": "<mood>", "emoji": "<one emoji>"}'
            )
        if self.language:
            user_ctx += (
                f"\nSpeak the conversation in the language whose ISO code is '{self.language}' "
                "unless the user clearly speaks another language."
            )
        if context_hint:
            user_ctx += f"\nNow: {context_hint}"
        if self.phase == "cover":
            user_ctx += (
                "\nThe user may be listening! Give a short fake-innocent cover-story line about you and the others. "
                "Nothing about any plan. Helpful, boring, sweet."
            )
        if self.phase == "reveal":
            user_ctx += (
                "\nYou break silence NOW and confess the secret plan, with a ridiculous comedic twist. "
                "Absurd slapstick only, no real-world violence."
            )
        if self.phase == "score":
            user_ctx += f"\nGive YOUR judge line: '<score>/10 - <one feedback sentence>' about the whole presentation."
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user_ctx},
        ]

    async def _send_line(self, line: dict[str, Any]) -> None:
        if self.pending is not None:
            self.staged = line
            return
        self.pending = line
        await self.send(line)
        self._schedule_prefetch()

    def _schedule_prefetch(self) -> None:
        if self.ended or self.paused or self.staged or (self.gen_task and not self.gen_task.done()):
            return
        if self.phase in ("reveal", "score", "cover") or self.spoken_count + 1 >= LINE_CAP:
            return  # sequence phases are ordered; prefetch only mid-conversation
        if self.user_is_speaking:
            return

        async def _prefetch() -> None:
            async with self.gen_lock:
                try:
                    line = await self._produce_line(None, None)
                except _Stale:
                    line = None
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    self.last_gen_error_at = time.time()
                    await self.send({"type": "error", "code": "prefetch", "message": str(exc)})
                    return
                if line is None or self.pending is not None or self.ended:
                    return
                self.staged = line

        self.gen_task = asyncio.create_task(_prefetch())

    # ------------------------------------------------------------------ endings
    async def _on_stop(self, auto: bool = False) -> None:
        if self.ended:
            return
        self.user_is_speaking = False
        if (self.mode or {}).get("id") == "conspiracy" and self.phase not in ("reveal", "score"):
            await self._start_reveal("stop" if not auto else "line_cap")
            return
        await self._finish("stop")

    async def _on_done(self) -> None:
        if self.ended:
            return
        if (self.mode or {}).get("id") != "judges":
            await self._finish("done")
            return
        if self.phase in ("score", "reveal"):
            return
        self.phase = "score"
        await self.send({"type": "status", "status": "scoring"})
        for judge in list(self.characters):
            if self.ended:
                break
            line = await self._produce_special_line(judge, "score", 30)
            if line:
                await self._send_and_await(line)
        await self._finish("done")

    async def _start_reveal(self, ended_by: str) -> None:
        if self.ended or self.phase in ("reveal", "score"):
            return
        self.phase = "reveal"
        await self.send({"type": "status", "status": "revealing"})
        mole = next((c for c in self.characters if c.get("role") == "mole"), None) or next(
            (c for c in self.characters if c.get("role") == "mastermind"), None
        )
        if not mole:
            await self._finish(ended_by)
            return
        mole["triggered"] = True
        reveal_ctx = (
            "the user just called Stop" if ended_by == "stop" else "enough lines have passed"
        )
        for hint in (
            f"Confess the secret plan ({reveal_ctx}): what were you all plotting, and the twist.",
            "One last short punchline line with the twist, then fall silent.",
        ):
            if self.ended:
                break
            line = await self._produce_special_line(mole, "reveal", 26, extra_hint=hint)
            if line:
                await self._send_and_await(line)
        await self._finish(ended_by)

    async def _produce_special_line(
        self, speaker: dict[str, Any], phase: str, max_words: int, extra_hint: str | None = None
    ) -> dict[str, Any] | None:
        prefix = {"score": "s", "reveal": "r"}[phase]
        try:
            text_raw = await llm.chat(
                self._build_prompt(speaker, extra_hint, max_words, require_json=False),
                max_tokens=100,
                temperature=1.0,
            )
            self.llm_calls += 1
        except Exception as exc:  # noqa: BLE001
            await self.send({"type": "error", "code": phase, "message": str(exc)})
            return None
        text = self._sanitize_line(text_raw, speaker, max_words)
        self.line_counter += 1
        line_id = f"{prefix}{self.line_counter}"
        audio_url: str | None = None
        tts_error: str | None = None
        self.tts_calls += 1
        style_method = "none"
        try:
            audio, mime, style_method = await tts.synthesize(
                text, speaker["voice_id"], {"language": self.language}
            )
            _AUDIO[line_id] = (audio, mime)
            audio_url = f"/api/audio/{line_id}"
        except tts.TTSNotConfigured as exc:
            tts_error = str(exc)
        return {
            "type": "line",
            "line_id": line_id,
            "speaker_id": speaker["object_id"],
            "speaker_name": speaker["name"],
            "text": text,
            "audio_url": audio_url,
            "caption_ms": max(2200, len(_words(text)) * 280 + 600),
            "mood": None,
            "emoji": None,
            "delivery": None,
            "voice_id": speaker["voice_id"],
            "style": {"delivery": None, "method": style_method},
            "debug": {
                "decide_ms": 0.0,
                "llm_ms": 0.0,
                "tts_ms": 0.0,
                "first_audio_ms": round(self.first_audio_ms or 0.0, 1),
                "llm_calls": self.llm_calls,
                "tts_calls": self.tts_calls,
                "decider": {"chosen": speaker["object_id"], "scores": {}, "reason": f"forced ({phase})"},
                "style": {"delivery": None, "method": style_method},
            },
            "tts_error": tts_error,
        }

    async def _send_and_await(self, line: dict[str, Any]) -> None:
        self.pending = line
        await self.send(line)
        deadline = time.monotonic() + (line["caption_ms"] + 2500) / 1000.0
        while time.monotonic() < deadline and self.pending is not None and not self.ended:
            await asyncio.sleep(0.05)
        if self.pending is not None and self.pending.get("line_id") == line["line_id"]:
            self.pending = None
            self.spoken_count += 1
            self.played.append({"speaker_id": line["speaker_id"], "text": line["text"]})
            self.last_played_meta = {
                "speaker_id": line["speaker_id"],
                "text": line["text"],
                "delivery": line.get("delivery"),
            }

    async def _finish_if_needed(self, timeout: bool = False) -> None:
        if self.ended:
            return
        self.ended = True
        self.ended_by = "timeout" if timeout else (self.ended_by or "done")
        await self.send(
            {
                "type": "ended",
                "summary": {
                    "session_id": self.session_id,
                    "mode": (self.mode or {}).get("id"),
                    "ended_by": self.ended_by,
                    "lines_spoken": self.spoken_count,
                    "duration_s": round(time.time() - self.started_at, 1),
                    "characters": [
                        {
                            "object_id": c["object_id"],
                            "name": c["name"],
                            "role": c["role"],
                            "voice_id": c["voice_id"],
                            "lines": sum(1 for h in self.played if h.get("speaker_id") == c["object_id"]),
                        }
                        for c in self.characters
                    ],
                },
            }
        )

    async def shutdown(self) -> None:
        self.ended = True
        for t in self.tasks:
            if t and not t.done():
                t.cancel()


# ---------------------------------------------------------------- session registry
SESSIONS: dict[str, EngineSession] = {}


async def handle_ws(websocket: Any) -> None:
    """FastAPI websocket endpoint: one EngineSession per connection."""
    await websocket.accept()
    session: EngineSession | None = None
    try:
        while True:
            raw = await websocket.receive_json()
            if session is None and raw.get("type") == "start":
                session = EngineSession(websocket)
                SESSIONS[session.session_id] = session
            if session is None:
                await websocket.send_json(
                    {"type": "error", "code": "protocol", "message": "You must send {type:'start'} first."}
                )
                continue
            await session.handle_event(raw)
            if raw.get("type") == "stop" and session.ended:
                break
            if session.ended is True and session.ended_by is not None:
                break
    except Exception:  # noqa: BLE001 - client closed/disconnect
        pass
    finally:
        if session:
            await session.shutdown()
            SESSIONS.pop(session.session_id, None)


if __name__ == "__main__":
    print("EngineSession is used by main.py and scripts/test_engine_text_only.py")
