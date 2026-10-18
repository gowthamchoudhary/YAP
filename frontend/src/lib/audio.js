// Strictly one speaker at a time. A line arrives with {line_id, audio_url,
// caption_ms, speaker_name}; we fetch + play it, and when it truly finishes we
// bark "played {line_id}" back to the engine. Barge-in (user speaks) calls
// stopAll() which stops the current clip instantly and clears the queue.
// A separate playOnce() path plays one-shot audio (e.g. a re-voiced replay)
// WITHOUT acknowledging it as played history.
import { audioUrl } from "./api.js";

export function createAudioBus({ onPlayed, onLineStart, onLineEnd }) {
  let ctx = null;           // unlocked AudioContext (fallback PCM decode path)
  let analyser = null;      // real level metering for the current clip
  let meterBuf = null;
  const queue = [];
  let current = null;       // { line, el | source, node, acked }
  let oneShot = null;       // { el | source, key } - replayed audio, no acks
  let paused = false;

  function unlock() {
    // must be called once from a user gesture (click)
    if (!ctx) {
      try {
        ctx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 24000 });
      } catch {
        ctx = new (window.AudioContext || window.webkitAudioContext)();
      }
    }
    if (ctx.state === "suspended") ctx.resume().catch(() => {});
    if (!analyser) {
      try {
        analyser = ctx.createAnalyser();
        analyser.fftSize = 256;
        meterBuf = new Uint8Array(analyser.fftSize);
      } catch {
        analyser = null;
      }
    }
  }

  function _finishCurrent() {
    const item = current;
    current = null;
    if (item?.el) {
      try {
        item.el.onended = null;
        item.el.pause();
        item.el.src = "";
      } catch {}
    }
    if (item?.source) {
      try {
        item.source.stop(0);
      } catch {}
    }
    if (item && !item.acked) {
      item.acked = true;
      onPlayed?.(item.line.line_id);
      onLineEnd?.(item.line);
    }
    if (!paused) _dequeue();
  }

  function _stopOneShot() {
    const shot = oneShot;
    oneShot = null;
    if (!shot) return;
    if (shot.el) {
      try {
        shot.el.onended = null;
        shot.el.pause();
        shot.el.src = "";
      } catch {}
    }
    if (shot.source) {
      try {
        shot.source.stop(0);
      } catch {}
    }
  }

  function _dequeue() {
    if (current || paused || queue.length === 0) return;
    const line = queue.shift();
    _play(line).catch(() => _finishCurrent());
  }

  async function _decodePcm(buffer, rate) {
    // fallback path for raw PCM16 (audio/pcm) responses
    const raw = buffer;
    const pcm = new Int16Array(raw);
    const audio = ctx.createBuffer(1, Math.floor(pcm.length / 2), rate);
    const ch = audio.getChannelData(0);
    for (let i = 0; i < audio.length; i++) ch[i] = pcm[i] / 32768;
    return audio;
  }

  function _hookAnalyserNode(node, toDestination) {
    // route the real playback signal through the analyser (no fake metering)
    if (!analyser) {
      node.connect(toDestination ? ctx.destination : ctx.destination);
      return;
    }
    node.connect(analyser);
    analyser.connect(ctx.destination);
  }

  async function _startElementPlayback(mime, raw) {
    const el = new Audio(URL.createObjectURL(new Blob([raw], { type: mime || "audio/mpeg" })));
    el.playbackRate = 1;
    if (ctx && analyser) {
      try {
        const src = ctx.createMediaElementSource(el);
        _hookAnalyserNode(src, false);
      } catch {
        // element already wired differently or source node refused; plain playback
      }
    }
    return el;
  }

  async function _play(line) {
    if (!line.audio_url) {
      // TTS-disabled / unconfigured: caption only; ack after the estimated caption
      current = { line, el: null, acked: false, timer: setTimeout(_finishCurrent, line.caption_ms || 2200) };
      onLineStart?.(line);
      return;
    }
    if (ctx && ctx.state === "suspended") await ctx.resume().catch(() => {});
    const resp = await fetch(audioUrl(line.audio_url));
    if (!resp.ok) throw new Error(`audio fetch ${resp.status}`);
    const mime = (resp.headers.get("content-type") || "").split(";")[0].trim();
    const raw = await resp.arrayBuffer();

    if (mime === "audio/pcm") {
      if (!ctx) throw new Error("audio/pcm received but AudioContext not unlocked");
      const buf = await _decodePcm(raw, 24000);
      const source = ctx.createBufferSource();
      source.buffer = buf;
      _hookAnalyserNode(source, false);
      const item = { line, source, acked: false };
      current = item;
      onLineStart?.(line);
      source.onended = () => _finishCurrent();
      source.start(0);
      return;
    }
    const el = await _startElementPlayback(mime, raw);
    current = { line, el, acked: false };
    onLineStart?.(line);
    el.onended = () => _finishCurrent();
    await el.play();
  }

  async function playOnce(line) {
    // one-shot replay (re-voice): no queue, no played acks; a new one-shot
    // replaces the previous and barge-in (stopAll) kills it instantly
    _stopOneShot();
    if (ctx && ctx.state === "suspended") await ctx.resume().catch(() => {});
    const resp = await fetch(audioUrl(line.audio_url));
    if (!resp.ok) throw new Error(`audio fetch ${resp.status}`);
    const mime = (resp.headers.get("content-type") || "").split(";")[0].trim();
    const raw = await resp.arrayBuffer();
    if (mime === "audio/pcm") {
      if (!ctx) throw new Error("audio/pcm received but AudioContext not unlocked");
      // re-decode each time in case the cached blob url was already revoked or
      // the previous one-shot has already been stopped/cleaned up.
      const buf = await _decodePcm(raw, 24000);
      const source = ctx.createBufferSource();
      source.buffer = buf;
      _hookAnalyserNode(source, false);
      oneShot = { source };
      source.onended = () => {
        if (oneShot?.source === source) oneShot = null;
      };
      source.start(0);
      return;
    }
    const el = await _startElementPlayback(mime, raw);
    oneShot = { el };
    el.onended = () => {
      if (oneShot?.el === el) oneShot = null;
    };
    await el.play();
  }

  function meterLevel() {
    // 0..1 real signal level of whatever the analyser hears (0 when silent)
    if (!analyser || !meterBuf) return 0;
    analyser.getByteTimeDomainData(meterBuf);
    let peak = 0;
    for (let i = 0; i < meterBuf.length; i++) {
      const dev = Math.abs(meterBuf[i] - 128) / 128;
      if (dev > peak) peak = dev;
    }
    return Math.min(1, peak * 1.6);
  }

  return {
    unlock,
    enqueue(line) {
      unlock();
      queue.push(line);
      _dequeue();
    },
    playOnce,
    meterLevel,
    stopAll() {
      // instant barge-in: current clip + queue + one-shot vanish; engine gets played acks
      _stopOneShot();
      const waiting = [...queue.map((l) => l.line_id)];
      queue.length = 0;
      const cur = current;
      if (cur) {
        cur.acked = true; // cut lines are NOT "played" history on the server
        _finishCurrent();
      }
      return { cut: cur?.line?.line_id, waiting };
    },
    setPaused(p) {
      paused = p;
      if (current?.el) {
        if (p) current.el.pause();
        else current.el.play().catch(() => {});
      }
      if (!p) _dequeue();
    },
    get paused() {
      return paused;
    },
    get playingNow() {
      return current?.line || null;
    },
  };
}
