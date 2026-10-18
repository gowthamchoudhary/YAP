# YAP

Voice-first bringing objects to life: photograph a desk, a garden, a bed… a vision
model finds the objects, you pick a mode and the objects, and each pick becomes an
independent voiced character with its own personality, speaking habit and voice.
Characters talk to each other and to you. Real speech in (AssemblyAI streaming
STT), real speech out (AssemblyAI TTS), real brains (Groq).

Modes (config-driven — a new mode is a new JSON file in `backend/modes/`):

| mode         | cap | what happens |
|--------------|-----|--------------|
| `conspiracy` | 4   | dark-comedy cartoon plot against the user. Barge-in: playback stops, everyone swaps to a fake-innocent cover story for 1–2 lines, then slides back into the plot after ~6 s quiet. After ~14 spoken lines *or* Stop, the **mole** breaks silence and confesses with a twist. |
| `judges`     | 3   | live pitch panel (technical, business, skeptic). They wait for an end-of-turn pause, then ONE pointed question. `Done` (or 3 min) → every judge: score /10 + one feedback sentence. Difficulty: gentle / normal / harsh. |

Content rules are baked into every conspiracy prompt: absurd slapstick only
("the lamp plans a blinding flash"), no graphic violence or real-world methods,
never targeting real people.

---

## Stack

- **backend/** — Python, FastAPI, uvicorn, httpx, websockets, python-dotenv, pydantic. Sessions in memory, no database.
- **frontend/** — React + Vite, plain JavaScript (no TypeScript).
- Text LLM: Groq (`GROQ_TEXT_MODEL`, default `openai/gpt-oss-120b`). Vision: Groq (`GROQ_VISION_MODEL`, default `qwen/qwen3.8-27b`). Both wrapped behind `backend/llm.py` so the provider can change.
- Speech out: `backend/tts.py` — AssemblyAI has no standalone TTS endpoint, so every line is spoken by opening one real Voice Agent session (wss://agents.assemblyai.com/v1/ws) whose `greeting` speaks exactly our text; see `ASSEMBLYAI_NOTES.md`.
- Speech in: `backend/stt.py` — WebSocket proxy to AssemblyAI streaming v3 (`wss://streaming.assemblyai.com/v3/ws`, mono 16 kHz PCM16, key in the Authorization header **without** Bearer, never reaching the browser).
- Decision engine: `backend/decider.py` — `FallbackDecider`, the local weighted heuristic that also models silence and the silent-mole gate.
- Every external call appends one JSON line to `backend/api_log.jsonl` (see "API usage log" below).

## Build order (each module is independently runnable)

llm → scene → characters → decider → engine (text-only) → tts → frontend → stt.

## Setup (Windows 10)

Run the backend (uvicorn) and frontend (vite dev) side by side in two windows:

**Window 1 — backend (FastAPI on :8000):**

```bat
cd YAP\backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

copy ..\env.example .env
notepad .env                              :: fill in GROQ_API_KEY + ASSEMBLYAI_API_KEY

python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
:: http://localhost:8000/api/health should return {"ok": true, ...}
```

**Window 2 — frontend (Vite on :5173, a NEW terminal window):**

```bat
cd YAP\frontend
npm install
:: optional: see env.local.example.md to point at a non-proxied backend (VITE_API_BASE);
:: the vite proxy handles plain local dev with no env file at all
npm run dev
:: open http://localhost:5173
```

Open http://localhost:5173 (localhost is a secure context, so the camera and mic work).
The Vite dev server proxies `/api` and `/ws` to `http://localhost:8000`
(override with `BACKEND_URL` or `VITE_API_BASE`). CORS on the backend already
allows `http://localhost:5173`.

**Sanity check BEFORE opening the UI** (proves the core engine loop works with
no backend/UI running):

```bat
cd YAP\backend
.venv\Scripts\activate
set TTS_DISABLED=1
python ..\scripts\test_engine_text_only.py ..\scripts\tmp_photo.jpg
:: expect ~12 character lines + the conspiracy reveal, no API errors
```

### Secrets / env vars (see `env.example`)

| var                     | needed for | default |
|-------------------------|-----------|---------|
| `GROQ_API_KEY`          | everything LLM (scene, characters, dialogue, scores) | — |
| `GROQ_TEXT_MODEL`       | text model id | `openai/gpt-oss-120b` |
| `GROQ_VISION_MODEL`     | vision model id | `qwen/qwen3.8-27b` |
| `ASSEMBLYAI_API_KEY`    | streaming STT + TTS (Voice Agent sessions) | — |
| `ASSEMBLYAI_TTS_URL`    | optional TTS endpoint override | `wss://agents.assemblyai.com/v1/ws` |
| `TTS_DISABLED=1`        | run the engine text-only (used by the engine test) | off |

A missing key ALWAYS surfaces as
`Missing required environment variable: <NAME>` — in API responses, engine `error`
events, and each test script — never as a lone 401.

## Module test scripts (each runs alone, all real calls, prints latency)

```bat
cd backend && .venv\Scripts\activate
python ..\scripts\test_llm.py                     :: text model round-trips (incl. json mode)
python ..\scripts\test_vision.py path\to\photo.jpg :: scene prompt through the vision model
python ..\scripts\test_tts.py                     :: 3 lines x 3 voices -> scripts\test_tts_out\*
cd backend && python -m uvicorn main:app --port 8000   :: needed for stt + engine tests
python ..\scripts\test_stt.py myvoice16k.wav      :: streams a wav through the live STT relay
python ..\scripts\test_decider.py                 :: offline heuristic scenario (no API calls)
python ..\scripts\test_engine_text_only.py [photo] :: full engine loop, TTS off, 12 lines + reveal
python apilog.py                                   :: one selftest line into backend\api_log.jsonl
```

## What you must fill in manually

1. **Pay attention to the billing model for TTS.** There is no standalone TTS
   endpoint: `backend/tts.py` implements synthesize() by opening ONE Voice Agent
   WebSocket session per line and speaking the line verbatim as the session's
   `greeting` (voice via `session.output.voice`, audio captured as PCM16/24 kHz
   from `reply.audio` events, session ended immediately with `session.end` so
   the agent's LLM loop never runs). Billing is **$4.50/hour prorated per
   second** from WebSocket open — every line is one full session — so the
   session-open / time-to-first-audio / total times are logged per call in
   `backend/api_log.jsonl` and `py scripts/test_tts.py` prints them. All
   protocol details and the endpoint list are documented in
   `ASSEMBLYAI_NOTES.md`.
2. **`backend/voices.json`** — seeded with the English voice IDs documented today
   (`alba, anna, charles, eve, george, jane, jean, mary, michael, paul, vera`).
   Replace with the official per-service voice list from the TTS docs. If it has
   fewer entries than selected characters you get a clear error asking you to add
   voices or select fewer objects.

## Known gaps / notes

- **TTS = one Voice Agent session per line** — see `ASSEMBLYAI_NOTES.md` for the
  billing model and how to read the per-line overhead out of `api_log.jsonl`; the
  proto is gated by the disk cache (`sha1(text+voice)`), so a repeated line never
  reopens a session. While no key is set, lines play as captions with
  `audio_url: null`; the engine still runs (test with `TTS_DISABLED=1`).
- **No sessions DB** — scenes, characters, and audio caches live in process
  memory + disk cache; a backend restart drops the "same photo, different mode"
  continuity.
- **Frontend proxy** — WebSocket/HTTP go through the Vite dev proxy to
  `localhost:8000`; for phone-on-LAN demos, run the backend on `0.0.0.0` and set
  `VITE_API_URL=http://<pc-ip>:8000` before `npm run dev`.
- **Streaming STT chunk sizes** — AssemblyAI rejects audio frames shorter than
  50 ms or longer than 1 s; the frontend sends exact 50 ms 1600-byte frames and
  the backend clamps silently.
- **Groq model drift** — `llama-3.3-70b-versatile` was retired from Groq's
  on-demand tier (verified live via the `/v1/models` list); the text default is
  now `openai/gpt-oss-120b` and the vision default `qwen/qwen3.8-27b`. Both are
  env vars (`GROQ_TEXT_MODEL` / `GROQ_VISION_MODEL`), so swap models without
  touching code; `scripts/list_models.py` prints what your key can use.
- **Anti-repetition & line caps** are enforced in-engine (Jaccard > 0.7 vs the
  last 8 lines triggers ONE regeneration; lines > 18 words / multi-sentence get
  truncated or re-asked, so final punctuation can occasionally be pragmatic).
- **`played` timing** — only played lines enter history; a barge-in line is
  discarded, and a regenerated stale line is dropped by snapshot check.
- **Windows menus** — the frontend never touches the mic until you explicitly
  unmute on the Stage screen (autoplay + permission policy friendly).
- **API usage log** — every call appends one JSON line to `backend/api_log.jsonl`
  (`service, endpoint, status, latency_ms, error, request_bytes/response_bytes`).
  Copy it into the hackathon README's "API usage" table with:
  `python -c "import json;[print(json.dumps(json.loads(l))) for l in open('backend/api_log.jsonl')]"`.

## Architecture (backend files)

```
backend/
  main.py        FastAPI app, CORS (localhost:5173), routes below, lifespan warm-up
  llm.py         chat(...) + vision(...): Groq wrapper, timing + apilog
  scene.py       POST /api/scene -> {scene_id, objects[{id,label,bbox,hint}]}, in-memory image store
  characters.py  ONE LLM call -> name/role/personality/habit/catchphrase/relationships + voices
  decider.py     choose_speaker(state) -> Decision; FallbackDecider (the only implementation)
  engine.py      one asyncio task per session: turn loop, barge-in, cover stories,
                 reveal / scoring phases, 40-line & 10-min hard caps
  tts.py         synthesize(text, voice_id, style) -> (bytes, mime, style_method); Voice Agent greeting session per line
  tts_capabilities.json  language gate for the frontend voice-language selector (you fill in)
  stt.py         /ws/stt proxy to AssemblyAI streaming v3 (key stays server-side)
  apilog.py      one JSON line per external call -> backend/api_log.jsonl
  modes/*.json   mode definitions (cap, roles, tone, rules, ending)
  voices.json    voice id pool (you replace with the official TTS list)
```

WebSocket protocol (`/ws/session`) — client sends
`start{scene_id,mode,object_ids,difficulty}`, `user_speech_start`,
`user_transcript{text,final}`, `user_reaction{emoji,target_id?}`, `played{line_id}`,
`revoice`, `language{code}`, `pause`, `resume`, `stop`, `done`; server sends
`characters{...}`, `line{line_id,speaker_id,text,audio_url,caption_ms,mood,emoji,delivery,voice_id,style,debug}`,
`silent{speaker_id,reason}`, `status`, `debug`, `ended{summary}`.
