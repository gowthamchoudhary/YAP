// Strictly one speaker at a time. A line arrives with {line_id, audio_url,
// caption_ms, speaker_name}; we fetch + play it, and when it truly finishes we
// bark "played {line_id}" back to the engine. Barge-in (user speaks) calls
// stopAll() which stops the current clip instantly and clears the queue.
import { audioUrl } from "./api.js";

export function createAudioBus({ onPlayed, onLineStart, onLineEnd }) {
  let ctx = null;           // unlocked AudioContext (fallback PCM decode path)
  const queue = [];
  let current = null;       // { line, el | node }
  let paused = false;
  let mutedUntilStop = false;

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

    let node = null;
    let el = null;
    if (mime === "audio/pcm") {
      if (!ctx) throw new Error("audio/pcm received but AudioContext not unlocked");
      const buf = await _decodePcm(raw, 24000);
      const source = ctx.createBufferSource();
      source.buffer = buf;
      source.connect(ctx.destination);
      const item = { line, source, acked: false };
      current = item;
      onLineStart?.(line);
      source.onended = () => _finishCurrent();
      source.start(0);
      return;
    }
    el = new Audio(URL.createObjectURL(new Blob([raw], { type: mime || "audio/mpeg" })));
    el.playbackRate = 1;
    current = { line, el, acked: false };
    onLineStart?.(line);
    el.onended = () => _finishCurrent();
    await el.play();
  }

  return {
    unlock,
    enqueue(line) {
      unlock();
      queue.push(line);
      _dequeue();
    },
    stopAll() {
      // instant barge-in: current clip + queue vanish; engine gets played acks
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
