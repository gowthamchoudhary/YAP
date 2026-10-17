// Mic -> /ws/stt. Captures at the browser's native rate, resamples to 16 kHz
// mono, converts to 16-bit PCM, and streams ~50 ms binary frames (800 samples
// = 1600 bytes) — exactly the chunk size AssemblyAI streaming v3 expects.
// AEC on, AGC on, browser noise-suppression OFF (the server-side Voice Focus
// handles noise; double-stacking eats real speech).
export function createMic({ onPartial, onFinal, onLevel, onFirstPartial }) {
  let socket = null;
  let stream = null;
  let actx = null;
  let processor = null;
  let sourceNode = null;
  let acc = [];              // float32 accumulation
  let accLen = 0;
  let sawFirstPartial = false;
  let running = false;
  let inputRate = 48000;

  function _encodeInt16(f32) {
    const out = new Int16Array(f32.length);
    for (let i = 0; i < f32.length; i++) {
      const s = Math.max(-1, Math.min(1, f32[i]));
      out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    return out;
  }

  function _resampleTo16k(input) {
    const g = 16000 / inputRate;
    const n = Math.floor(input.length * g);
    const out = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      const pos = i / g;
      const i0 = Math.floor(pos);
      const frac = pos - i0;
      const a = input[i0] ?? 0;
      const b = input[i0 + 1] ?? a;
      out[i] = a + (b - a) * frac;
    }
    return out;
  }

  function _flushFrame() {
    const frame = new Int16Array(accLen);
    let k = 0;
    for (let i = 0; i < acc.length; i++) {
      const chunk = acc[i];
      frame.set(chunk, k);
      k += chunk.length;
    }
    acc = [];
    accLen = 0;
    if (socket && socket.readyState === 1) socket.send(frame.buffer);
  }

  function _handleAudio(e) {
    const input = e.inputBuffer.getChannelData(0);
    let level = 0;
    for (let i = 0; i < input.length; i += 8) level += input[i] * input[i];
    onLevel?.(Math.sqrt(level / (input.length / 8)));
    const down = _resampleTo16k(input);
    acc.push(down);
    accLen += down.length;
    while (accLen >= 800) {
      // flatten and take an exact 800-sample (50 ms) frame
      const flat = new Float32Array(accLen);
      let k = 0;
      for (const chunk of acc) {
        flat.set(chunk, k);
        k += chunk.length;
      }
      const frame = flat.subarray(0, 800);
      const rest = flat.slice(800);
      acc = rest.length ? [rest] : [];
      accLen = rest.length;
      const pcm = _encodeInt16(frame).buffer;
      if (socket && socket.readyState === 1) socket.send(pcm);
    }
  }

  async function connect() {
    if (running) return;
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, autoGainControl: true, noiseSuppression: false },
    });
    actx = new (window.AudioContext || window.webkitAudioContext)();
    inputRate = actx.sampleRate;
    sourceNode = actx.createMediaStreamSource(stream);
    processor = actx.createScriptProcessor(2048, 1, 1);
    processor.onaudioprocess = _handleAudio;
    sourceNode.connect(processor);
    processor.connect(actx.destination); // ScriptProcessor needs a sink

    const proto = location.protocol === "https:" ? "wss" : "ws";
    socket = new WebSocket(`${proto}://${location.host}/ws/stt`);
    socket.binaryType = "arraybuffer";
    socket.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === "partial" && msg.text) {
          if (!sawFirstPartial) {
            sawFirstPartial = true;
            onFirstPartial?.();
          }
          onPartial?.(msg.text);
        } else if (msg.type === "final" && msg.text) {
          onFinal?.(msg.text);
        } else if (msg.type === "error") {
          console.error("[stt]", msg.message);
        }
      } catch {}
    };
    running = true;
  }

  function disconnect() {
    running = false;
    sawFirstPartial = false;
    try {
      if (socket && socket.readyState === 1) socket.send(JSON.stringify({ type: "stop" }));
      socket && socket.close();
    } catch {}
    socket = null;
    try {
      stream?.getTracks().forEach((t) => t.stop());
      sourceNode?.disconnect();
      processor?.disconnect();
      actx?.close();
    } catch {}
    stream = actx = processor = sourceNode = null;
    onLevel?.(0);
  }

  function resetTurnFlags() {
    sawFirstPartial = false;
  }

  return { connect, disconnect, resetTurnFlags, get running() { return running; } };
}
