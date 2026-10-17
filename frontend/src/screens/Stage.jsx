import { useCallback, useEffect, useRef, useState } from "react";
import { createAudioBus } from "../lib/audio.js";
import { createMic } from "../lib/mic.js";
import { wsUrl } from "../lib/api.js";

// per-character hue used for glow colouring
const hueFor = (i) => (i * 83) % 360;

export default function StageScreen({
  sceneData,
  modeId,
  modeName,
  difficulty,
  selected,
  onChangeMode,
  onRetake,
  onDebug,
  onEnded,
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

  const engineWs = useRef(null);
  const busRef = useRef(null);
  const micRef = useRef(null);
  const startedRef = useRef(false);
  const photoUrl = sceneData.image_blob ? URL.createObjectURL(sceneData.image_blob) : null;

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
        send({ type: "user_transcript", text, final: true });
      },
      onLevel: (lvl) => setMicLevel(lvl),
      onFirstPartial: () => {
        send({ type: "user_speech_start" });
        bus.stopAll(); // instant barge-in
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
          setCharacters(msg.characters.map((c, i) => ({ ...c, hue: hueFor(i) })));
          setStatus("live");
          break;
        case "line":
          bus.enqueue(msg); // caption-only clips ack after caption_ms inside the bus
          setCaptions((c) => [...c, { who: msg.speaker_name, text: msg.text }]);
          if (msg.tts_error) setError(`TTS: ${msg.tts_error}`);
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
      try {
        ws.close();
      } catch {}
      mic.disconnect();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ------------------------------------------------------------------ controls
  const toggleMic = useCallback(async () => {
    const mic = micRef.current;
    try {
      if (mic.running) {
        mic.disconnect();
        setMuted(true);
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  });

  const onPause = useCallback(() => {
    const p = !busRef.current?.paused;
    busRef.current?.setPaused(p);
    send({ type: p ? "pause" : "resume" });
    setStatus(p ? "paused" : "live");
  });

  const onStop = useCallback(() => {
    busRef.current?.stopAll();
    send({ type: "stop" });
  });

  const onDone = useCallback(() => {
    busRef.current?.stopAll();
    send({ type: "done" });
  });

  const isJudges = modeId === "judges";

  return (
    <section className="screen stage">
      <header className="screen-head">
        <span className="accent-text">{modeName}</span>
        <span className="status">{status}</span>
      </header>

      <div className="stage-photo-wrap">
        {photoUrl && <img src={photoUrl} alt="The stage" className="scene-photo dimmed" />}
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
              <button className="btn ghost" onClick={onRetake}>
                New photo
              </button>
            </div>
          </div>
        )}
      </div>

      <div className="caption-bar" aria-live="polite">
        {liveCaption ? (
          <>
            <strong>{liveCaption.speaker_name}</strong>
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

      <div className="controls">
        <button className="btn" onClick={onPause} disabled={!!ended}>
          pause / resume
        </button>
        <button className="btn danger" onClick={onStop} disabled={!!ended}>
          Stop
        </button>
        <button className="btn" onClick={toggleMic}>
          {muted ? "unmute mic" : "mute mic"}
        </button>
        {isJudges && (
          <button className="btn primary" onClick={onDone} disabled={!!ended}>
            Done — get scores
          </button>
        )}
      </div>
      <div className="mic-meter" aria-hidden>
        <div style={{ width: `${Math.min(100, micLevel * 300)}%` }} />
      </div>
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
