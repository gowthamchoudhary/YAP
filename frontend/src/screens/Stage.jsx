import { useCallback, useEffect, useRef, useState } from "react";
import { createAudioBus } from "../lib/audio.js";
import { createMic } from "../lib/mic.js";
import { wsUrl, API_BASE } from "../lib/api.js";

// per-character hue used for glow colouring
const hueFor = (i) => (i * 83) % 360;

// reaction + line-face emoji whitelist (mirrors the engine's EMOJI_WHITELIST)
const EMOJIS = ["😈", "😏", "😳", "😱", "🤔", "😅", "🙄", "😴", "👀", "🔥", "💡", "🎤", "❓", "✅", "❌", "👏"];

/**
 * Spotlight states (conspiracy): "normal" | "off" | "innocent" | "reveal".
 * - normal: every character's bbox gets a soft elliptical light; the speaker's
 *   brightens/widens while listeners sit at ~40%.
 * - off: the user is speaking — everything cuts to black.
 * - innocent: after ~300 ms of user speech, one warm "innocent" light takes over
 *   during the cover story (until the engine reports "back to plotting").
 * - reveal: a single spotlight stays on the mole, everything else dark.
 */
export default function StageScreen({
  sceneData,
  modeId,
  modeName,
  difficulty,
  selected,
  characterEdits, // {object_id: {name, traits, voice_id}} from the Setup screen
  onChangeMode,
  onRetake,
  onDebug,
  onEnded,
  onBackHome,
}) {
  const [characters, setCharacters] = useState([]);
  const [captions, setCaptions] = useState([]);
  const [liveCaption, setLiveCaption] = useState(null);
  const [speakerId, setSpeakerId] = useState(null);
  const [status, setStatus] = useState("starting");
  const [muted, setMuted] = useState(true);
  const [micLevel, setMicLevel] = useState(0);
  const [ended, setEnded] = useState(null);
  const [error, setError] = useState("");
  // ---- feature pass 2
  const [floaters, setFloaters] = useState([]); // [{id, emoji, targetId}] max 3
  const [userSpeaking, setUserSpeaking] = useState(false);
  const [flickerId, setFlickerId] = useState(null); // silent char re-entry flicker
  const [lineMeta, setLineMeta] = useState({}); // line_id -> {delivery, ttsMs, voiceId, style}
  const [lastLineBy, setLastLineBy] = useState({}); // speaker_id -> last line msg
  const [hasLastLine, setHasLastLine] = useState(false);
  const [voiceLabOpen, setVoiceLabOpen] = useState(false);
  const [labLevel, setLabLevel] = useState(0);
  const [stats, setStats] = useState({ voices: new Set(), deliveries: new Set(), ttsMs: [] });
  const [caps, setCaps] = useState(null); // {languages_supported, languages}
  const [judgeTone, setJudgeTone] = useState("warm"); // judges light shifts per score line

  const engineWs = useRef(null);
  const busRef = useRef(null);
  const micRef = useRef(null);
  const startedRef = useRef(false);
  const floaterSeq = useRef(0);
  const spokenOnce = useRef(new Set());
  const timers = useRef([]);
  const photoUrl = sceneData.image_blob ? URL.createObjectURL(sceneData.image_blob) : null;

  // ------------------------------------------------------------------ helpers
  const later = (fn, ms) => {
    const t = setTimeout(fn, ms);
    timers.current.push(t);
    return t;
  };
  const clearTimers = () => {
    for (const t of timers.current) clearTimeout(t);
    timers.current = [];
  };

  const pushFloater = (emoji, targetId) => {
    if (!emoji) return;
    const id = ++floaterSeq.current;
    setFloaters((f) => [...f.slice(-2), { id, emoji, targetId }]); // keep max 3 on screen
    later(() => setFloaters((f) => f.filter((x) => x.id !== id)), 2400);
  };

  useEffect(() => {
    fetch(`${API_BASE}/api/tts_capabilities`)
      .then((r) => (r.ok ? r.json() : null))
      .then(setCaps)
      .catch(() => setCaps(null));
    return () => {};
  }, []);

  // ------------------------------------------------------------------ setup
  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;

    // audio bus: one speaker at a time, instant stop on barge-in, `played` acks
    const bus = createAudioBus({
      onPlayed: (lineId) => send({ type: "played", line_id: lineId }),
      onLineStart: (line) => {
        setSpeakerId(line.speaker_id);
        setLiveCaption(line);
        // silent char re-entering after silence: brief flicker of its spotlight
        const ch = charactersRef.current.find((c) => c.object_id === line.speaker_id);
        if (ch?.starts_silent && !spokenOnce.current.has(ch.object_id)) {
          spokenOnce.current.add(ch.object_id);
          setFlickerId(ch.object_id);
          later(() => setFlickerId(null), 1100);
        }
        if (!line.replay) {
          pushFloater(line.emoji, line.speaker_id);
          if (line.delivery) {
            setLineMeta((m) => ({
              ...m,
              [line.line_id]: {
                delivery: line.delivery,
                ttsMs: line.debug?.tts_ms,
                voiceId: line.voice_id,
                styleMethod: line.style?.method,
              },
            }));
          }
          setStats((s) => ({
            voices: new Set([...s.voices, line.voice_id].filter(Boolean)),
            deliveries: new Set([...s.deliveries, line.delivery].filter(Boolean)),
            ttsMs: line.debug?.tts_ms != null ? [...s.ttsMs, line.debug.tts_ms] : s.ttsMs,
          }));
          setLastLineBy((m) => ({ ...m, [line.speaker_id]: line }));
          setHasLastLine(true);
        }
      },
      onLineEnd: () => {
        setSpeakerId(null);
        setLiveCaption(null);
      },
    });
    bus.unlock(); // user clicked to get here, so the AudioContext may start
    busRef.current = bus;

    // mic: streams 16k mono PCM to /ws/stt; first partial = user_speech_start
    const mic = createMic({
      onPartial: (text) => setLiveCaption({ speaker_name: "you (live)", text }),
      onFinal: (text) => {
        setCaptions((c) => [...c, { who: "you", text }]);
        setUserSpeaking(false);
        send({ type: "user_transcript", text, final: true });
      },
      onLevel: (lvl) => setMicLevel(lvl),
      onFirstPartial: () => {
        send({ type: "user_speech_start" });
        setUserSpeaking(true); // lighting: all spotlights cut while you talk
        busRef.current?.stopAll(); // instant barge-in
        setSpeakerId(null);
      },
    });
    micRef.current = mic;

    const ws = new WebSocket(wsUrl("/ws/session"));
    engineWs.current = ws;
    ws.onopen = () => {
      send({
        type: "start",
        scene_id: sceneData.scene_id,
        mode: modeId,
        object_ids: selected,
        difficulty,
        // Setup-screen personality edits: names, trait chips and voices the user
        // set before starting — the engine merges them into each character's prompt.
        ...(characterEdits && Object.keys(characterEdits).length ? { character_edits: characterEdits } : {}),
      });
    };
    ws.onmessage = (ev) => {
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      onDebug?.({ kind: "ws-in", msg });
      switch (msg.type) {
        case "characters":
          charactersRef.current = msg.characters.map((c, i) => ({ ...c, hue: hueFor(i) }));
          setCharacters(charactersRef.current);
          setStatus("live");
          break;
        case "line":
          if (msg.replay) {
            // re-voiced replay: one-shot playback, never acked or captioned
            busRef.current?.playOnce(msg);
          } else {
            if (modeId === "judges" && /\d+\s*\/\s*10/.test(msg.text || "")) {
              judgesScoreTone.current = judgesScoreTone.current === "warm" ? "cold" : "warm";
              setJudgeTone(judgesScoreTone.current);
            }
            bus.enqueue(msg); // caption-only clips ack after caption_ms inside the bus
            setCaptions((c) => [...c, { who: msg.speaker_name, text: msg.text }]);
            if (msg.tts_error) setError(`TTS: ${msg.tts_error}`);
          }
          break;
        case "silent":
          setStatus(msg.reason);
          break;
        case "status":
          setStatus(msg.status);
          break;
        case "ended":
          setEnded(msg.summary);
          setStatus("ended");
          break;
        case "error":
          setError(msg.message || "engine error");
          break;
        default:
          break;
      }
    };
    ws.onerror = () => setError("Lost the connection to the backend.");
    ws.onclose = () =>
      setStatus((s) => (s === "ended" ? s : "disconnected — is the backend running?"));

    // NOTE: connect the mic only when the user first unmutes (autoplay policies + explicit consent)
    function send(obj) {
      if (ws.readyState === 1) {
        ws.send(JSON.stringify(obj));
        onDebug?.({ kind: "ws-out", msg: obj });
      }
    }
    return () => {
      clearTimers();
      try {
        ws.close();
      } catch {}
      mic.disconnect();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const charactersRef = useRef([]);
  const judgesScoreTone = useRef("warm");

  // ------------------------------------------------------------------ controls
  const toggleMic = async () => {
    const mic = micRef.current;
    try {
      if (mic.running) {
        mic.disconnect();
        setMuted(true);
        setUserSpeaking(false);
      } else {
        await mic.connect();
        busRef.current?.unlock();
        setMuted(false);
        setStatus("listening to you");
        send({ type: "resume" });
      }
    } catch (e) {
      setError(`Mic: ${e.message || e}`);
    }
  };

  const onPause = () => {
    const p = !busRef.current?.paused;
    busRef.current?.setPaused(p);
    send({ type: p ? "pause" : "resume" });
    setStatus(p ? "paused" : "live");
  };

  const onStop = () => {
    busRef.current?.stopAll();
    send({ type: "stop" });
  };

  const onDone = () => {
    busRef.current?.stopAll();
    send({ type: "done" });
  };

  const onReaction = (emoji) => {
    pushFloater(emoji, null); // the user's own reaction pops too
    send({ type: "user_reaction", emoji });
  };

  const onRevoice = () => send({ type: "revoice" });

  const onLanguage = (code) => {
    if (code) send({ type: "language", code });
  };

  // key V toggles the Voice Lab drawer
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "v" || e.key === "V") setVoiceLabOpen((v) => !v);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // live level meter while the Voice Lab is open
  useEffect(() => {
    if (!voiceLabOpen) return;
    const t = setInterval(() => setLabLevel(busRef.current?.meterLevel?.() || 0), 100);
    return () => clearInterval(t);
  }, [voiceLabOpen]);

  // ------------------------------------------------------------------ lighting state machine
  const isJudges = modeId === "judges";
  const revealing = status === "revealing";
  const coverStorying = modeId === "conspiracy" && status === "listening"; // user just spoke
  const mole = characters.find((c) => c.role === "mole");
  const coverLight = coverStorying && !userSpeaking; // innocent light after the 300 ms cut

  function spotClassFor(c) {
    if (modeId === "conspiracy" && revealing && mole) {
      // mole reveal: a single spotlight on the mole, everything else dark
      return c.object_id === mole.object_id && speakerId === mole.object_id ? "speaking" : "off";
    }
    if (userSpeaking || coverLight) return "off";
    if (speakerId === c.object_id) return "speaking";
    return "listening";
  }

  // ------------------------------------------------------------------ spotlight geometry
  const bboxOf = (object_id) => {
    const obj = (sceneData.objects || []).find((o) => o.id === object_id);
    if (!obj || !Array.isArray(obj.bbox) || obj.bbox.length < 4) {
      return { left: "45%", top: "45%", width: "12%", height: "16%" };
    }
    const [x, y, w, h] = obj.bbox;
    // elliptical soft light, a touch larger than the bbox
    const padX = Math.max(0.04, w * 0.35);
    const padY = Math.max(0.06, h * 0.45);
    return {
      left: `${Math.max(0, (x - padX) * 100)}%`,
      top: `${Math.max(0, (y - padY) * 100)}%`,
      width: `${(w + padX * 2) * 100}%`,
      height: `${(h + padY * 2) * 100}%`,
    };
  };

  const avgTts =
    stats.ttsMs.length > 0
      ? Math.round(stats.ttsMs.reduce((a, b) => a + b, 0) / stats.ttsMs.length)
      : null;

  return (
    <section className="screen stage">
      <header className="screen-head">
        <span className="accent-text">{modeName}</span>
        <span className="status">{status}</span>
      </header>

      <div className="stage-photo-wrap">
        {photoUrl && <img src={photoUrl} alt="The stage" className="scene-photo dimmed" />}

        {/* whole-photo dim, per-mode; judges shifts warm/cold as scores come in */}
        {characters.length > 0 && (
          <div
            className={`stage-dim ${modeId === "conspiracy" ? "conspiracy" : ""} ${
              isJudges ? (judgeTone === "warm" ? "judges-warm" : "judges-cold") : ""
            } ${modeId === "conspiracy" && !userSpeaking && !revealing ? "flicker" : ""}`}
            aria-hidden
          />
        )}

        {/* per-character elliptical spotlights */}
        {characters.map((c) => (
          <div
            key={c.object_id} className={`spot ${spotClassFor(c)} ${flickerId === c.object_id ? "reentry" : ""}`}
            style={bboxOf(c.object_id)}
            aria-hidden
          >
            {floaters
              .filter((f) => f.targetId === c.object_id)
              .map((f) => (
                <span key={f.id} className="emoji-float" style={{ left: "40%", top: "-18px" }}>
                  {f.emoji}
                </span>
              ))}
          </div>
        ))}

        {/* conspiracy cover story: one warm innocent light replaces the spotlights */}
        {modeId === "conspiracy" && (userSpeaking || coverLight) && (
          <div
            className={`spot ${coverLight ? "innocent-light speaking" : "off"}`}
            style={{ left: "28%", top: "22%", width: "44%", height: "50%" }}
            aria-hidden
          />
        )}

        {/* user's own floaters (reactions with no target) */}
        {floaters
          .filter((f) => !f.targetId)
          .map((f) => (
            <span key={f.id} className="emoji-float" style={{ left: "48%", top: "10%" }}>
              {f.emoji}
            </span>
          ))}

        {characters.map((c) => {
          const speaking = speakerId === c.object_id;
          return (
            <div
              key={c.object_id}
              className={`char-overlay ${speaking ? "speaking" : "listening"} ${
                c.starts_silent && !speaking ? "silent" : ""
              }`}
              style={{ ["--c"]: `hsl(${c.hue} 90% 62%)` }}
              title={`${c.name} — ${c.role}`}
            >
              <span className="char-ring" />
              <span className="char-name">{c.name}</span>
            </div>
          );
        })}
        {ended && (
          <div className="ended-card">
            <strong>session ended — {ended.ended_by}</strong>
            {ended.characters && (
              <ul>
                {ended.characters.map((c) => (
                  <li key={c.object_id}>
                    {c.name} ({c.role}) — {c.lines} line{c.lines === 1 ? "" : "s"}
                  </li>
                ))}
              </ul>
            )}
            <div className="row">
              <button className="btn" onClick={onChangeMode}>
                Same photo, different mode
              </button>
              <button className="btn" onClick={onRetake}>
                New photo
              </button>
              <button className="btn" onClick={onBackHome}>
                Home
              </button>
            </div>
          </div>
        )}
      </div>

      <div className="caption-bar" aria-live="polite">
        {liveCaption ? (
          <>
            <strong>{liveCaption.speaker_name}</strong>
            {liveCaption.delivery && <span className="dim"> · {liveCaption.delivery}</span>}
            <p>{liveCaption.text}</p>
          </>
        ) : (
          <p className="dim">
            {isJudges
              ? "Start pitching out loud — the judges are waiting for a pause."
              : "The objects are plotting. Say something and hear how fast they snap innocent."}
          </p>
        )}
      </div>

      <div className="transcript">
        {captions.map((c, i) => (
          <p key={i}>
            <strong>{c.who}</strong> {c.text}
          </p>
        ))}
      </div>

      {/* reaction bar (same whitelist as the engine) */}
      {!ended && (
        <div className="reaction-bar" role="toolbar" aria-label="React to the conversation">
          {EMOJIS.map((em) => (
            <button key={em} onClick={() => onReaction(em)} aria-label={`react ${em}`}>
              {em}
            </button>
          ))}
        </div>
      )}

      <div className="controls">
        <button className="btn" onClick={onPause} disabled={!!ended}>
          pause / resume
        </button>
        <button className="btn danger" onClick={onStop} disabled={!!ended}>
          Stop
        </button>
        <button className="btn" onClick={() => toggleMic().catch(() => {})}>
          {muted ? "unmute mic" : "mute mic"}
        </button>
        {isJudges && (
          <button className="btn primary" onClick={onDone} disabled={!!ended}>
            Done — get scores
          </button>
        )}
        {caps?.languages_supported && caps?.languages?.length > 0 && (
          <label className="dim">
            language{" "}
            <select className="chip" onChange={(e) => onLanguage(e.target.value)} defaultValue="">
              <option value="" disabled>
                off
              </option>
              {caps.languages.map((l) => (
                <option key={l} value={l}>
                  {l}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      <div className="mic-meter" aria-hidden>
        <div style={{ width: `${Math.min(100, micLevel * 300)}%` }} />
      </div>

      {/* session stats strip */}
      <div className="stats-strip">
        <span>
          distinct voices: <b>{stats.voices.size}</b>
        </span>
        <span>
          deliveries: <b>{[...stats.deliveries].join(", ") || "—"}</b>
        </span>
        <span>
          avg TTS latency: <b>{avgTts != null ? `${avgTts} ms` : "—"}</b>
        </span>
        <span className="dim">
          press <kbd>V</kbd> for the Voice Lab
        </span>
      </div>

      {voiceLabOpen && (
        <div className="voice-lab" role="dialog" aria-label="Voice Lab">
          <header>
            <strong>Voice Lab</strong>
            <button className="btn ghost" onClick={() => setVoiceLabOpen(false)}>
              close
            </button>
          </header>
          <p className="dim">current line level (real analyser on the playing audio)</p>
          <div className="line-meter" aria-hidden>
            <div style={{ width: `${Math.min(100, labLevel * 100)}%` }} />
          </div>
          {characters.map((c) => {
            const meta = lastLineBy[c.object_id];
            const lm = meta ? lineMeta[meta.line_id] : null;
            return (
              <div className="voice-row" key={c.object_id}>
                <b>{c.name}</b>
                <span className="voice-id">{c.voice_id}</span>
                <span className="dim">{c.speaking_habit}</span>
                {meta && (
                  <span className="dim">
                    last delivery: {meta.delivery || "normal"} · tts {lm?.ttsMs ?? meta.debug?.tts_ms} ms
                    {lm?.styleMethod ? ` · style: ${lm.styleMethod}` : ""}
                  </span>
                )}
                <button className="btn ghost" onClick={onRevoice} disabled={!hasLastLine}>
                  re-voice last line
                </button>
              </div>
            );
          })}
        </div>
      )}

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </section>
  );

  function send(obj) {
    const ws = engineWs.current;
    if (ws && ws.readyState === 1) {
      ws.send(JSON.stringify(obj));
      onDebug?.({ kind: "ws-out", msg: obj });
    }
  }
}
