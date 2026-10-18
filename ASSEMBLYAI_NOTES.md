# AssemblyAI voice notes (this project)

Everything below was checked against the AssemblyAI Voice Agent API docs list
at the end. No protocol message name on this page is invented; each one is
copied from the docs.

## Endpoints and the basic truth

- **There is NO standalone text-to-speech HTTP endpoint at AssemblyAI.**
  Speech output exists only inside the **Voice Agent API** — one WebSocket
  session that bundles STT + LLM + TTS:
  `wss://agents.assemblyai.com/v1/ws`
- The documented event flow per session is:
  open → client `session.update` → server `session.ready` → (optional)
  client `input.audio` → `reply.started` → `reply.audio` (base64 chunks) →
  `reply.done` → client `session.end` → server `session.ended`.
- Auth (server-side client): the API key goes as a **Bearer token in the
  Authorization header of the WebSocket upgrade request**. (Browsers can't send
  headers, so for a browser you'd fetch a single-use token from
  `GET https://agents.assemblyai.com/v1/token` and pass `?token=<token>` — not
  needed here since we connect from Python.)

## The greeting workaround (how backend/tts.py speaks exact text)

The only documented way to make the agent say *and only* our text is the
session's `greeting` field, which the docs say is **spoken on connect, as soon
as the session is ready**, without requiring any user audio first:

```json
{
  "type": "session.update",
  "session": {
    "greeting": "<the line to speak, verbatim>",
    "output": { "voice": "<voice_id>", "format": { "encoding": "audio/pcm" } }
  }
}
```

The exact inline field paths are `session.greeting` and `session.output.voice`;
the docs say both are immutable after `session.ready`, so they must be set in the
first `session.update` (before `session.ready`). Inline `session.greeting` +
`session.output.voice` are mutually exclusive with `agent_id` (stored-agent
binding); we never send `agent_id`, so the session is fully inline.

Output audio arrives as `reply.audio` JSON events with a base64 `data` field:
monaural **PCM16, 24,000 Hz, little-endian** by default
(session.output.format.encoding). The client does **not** send `input.audio`,
so the agent's listening loop never gets a user utterance — the greeting is the
only thing that is ever spoken. After `reply.done` the client immediately sends
`{"type": "session.end"}` and waits for `session.ended`, closing the session
before the agent's own LLM loop could ever engage.

The docs define no separate "greeting finished" event; `reply.done` (the
documented reply-completion event) is used as the end signal, with a
2.5-sound-quiet fallback in `tts.py` in case it were ever delayed.

## Billing model

- Published price: **$4.50/hour** (= $0.075/minute), **duration-based and
  prorated to the second** — NOT a flat fee per session.
- Billing starts the moment the WebSocket opens: idle time counts, and a bare
  socket close leaves a **billable 30-second resume grace window** — which is
  exactly why `tts.py` always terminates with `session.end` instead of just
  closing the socket.
- Means: **every spoken line of the game is one full Voice Agent session.**## Per-line session overhead (measured)

Every synthesize() call logs four rows to `backend/api_log.jsonl`
(one JSON line each, see apilog.py):

| service            | latency_ms meaning                                  |
|--------------------|-----------------------------------------------------|
| `tts-session-open` | time to open the WebSocket                          |
| `tts-session-ready`| session.open → `session.ready`                      |
| `tts-first-audio`  | session open → **first audio byte** (TTFB)          |
| `tts`              | total session time, plus `response_bytes` = audio size|

Measured live (2026-10, same workspace key, 9-word line, outward latency
included): session **open ≈ 110–170 ms**, open→`session.ready` ≈ 370–685 ms,
open→**first audio byte ≈ 560–900 ms**, total session ≈ **3.8–4.4 s**
(≈143–156 KB of PCM = ~3 s of audio for the 9-word line). In other words: for
this line size, the session pays roughly **0.6–0.9 s before the first audio
byte and ~1 s of trailing silence/teardown on top of the speech itself** — the
cost of a full agent session instead of a REST call. The line-duration audio
itself scales with the text length as expected.

Repeated identical (text, voice) pairs never reopen a session: results are
disk-cached in `backend/.tts_cache/` keyed by `sha1(voice␀text)`.

Read the rows straight out of `api_log.jsonl` with:

```bat
python -c "import json;[print(l.strip()) for l in open('backend/api_log.jsonl') if '\"tts' in l]"
```

## Cost per line (formula, in terms the log gives you)

`cost_per_line = total_ms/1000 / 3600 * 4.50` dollars — e.g. a 2.5 s session
is ≈ $0.0031, a 4 s session ≈ $0.0050 per line. A 40-line worst-case game run
is therefore at most ≈ $0.20 of TTS — but ONLY if every session is terminated
promptly; un-terminated or idle-open sessions bill at the same rate for as
long as they stay open.

## Where our usage lives

- `backend/tts.py` — the ONLY place that opens these sessions; cache + telemetry as above.
- `backend/voices.json` — voice IDs from the documented examples (`alba`, …).
- `backend/tts_capabilities.json` — language gate for the frontend selector
  (the greeting workaround has no language control; leave
  `languages_supported: false` unless the API proves otherwise).

## Docs read

- https://www.assemblyai.com/docs/voice-agents/voice-agent-api
- https://www.assemblyai.com/docs/voice-agents/voice-agent-api/session-configuration
- https://www.assemblyai.com/docs/voice-agents/voice-agent-api/greeting
- https://www.assemblyai.com/docs/voice-agents/voice-agent-api/events-reference
- https://www.assemblyai.com/docs/voice-agents/voice-agent-api/api-spec/voice-agent-websocket
- https://www.assemblyai.com/docs/voice-agents/voice-agent-api/audio-format
- https://www.assemblyai.com/docs/voice-agents/voice-agent-api/browser-integration
- https://www.assemblyai.com/docs/voice-agents/voice-agent-api/deploy
- https://www.assemblyai.com/docs/billing-and-pricing and
  https://www.assemblyai.com/pricing
