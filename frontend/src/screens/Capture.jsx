import { useCallback, useEffect, useRef, useState } from "react";
import { createScene } from "../lib/api.js";

export default function CaptureScreen({ onImageReady }) {
  const videoRef = useRef(null);
  const canvasRef = useRef(null);
  const streamRef = useRef(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [ready, setReady] = useState(false);

  const stopStream = useCallback(() => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
  }, []);

  const openCamera = useCallback(async () => {
    setError("");
    try {
      stopStream();
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 }, height: { ideal: 1080 } },
        audio: false,
      });
      streamRef.current = stream;
      videoRef.current.srcObject = stream;
      await videoRef.current.play();
      setReady(true);
    } catch (e) {
      setReady(false);
      setError(`Camera unavailable (${e.name}). Upload a photo instead.`);
    }
  }, [stopStream]);

  useEffect(() => {
    openCamera();
    return stopStream;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const capture = useCallback(async () => {
    setBusy(true);
    setError("");
    try {
      const video = videoRef.current;
      canvasRef.current.width = video.videoWidth || 1280;
      canvasRef.current.height = video.videoHeight || 720;
      canvasRef.current.getContext("2d").drawImage(video, 0, 0);
      const blob = await new Promise((res) => canvasRef.current.toBlob(res, "image/jpeg", 0.9));
      stopStream();
      const data = await createScene(blob);
      onImageReady({ ...data, image_blob: blob });
    } catch (e) {
      setError(String(e.message || e));
      setBusy(false);
      openCamera(); // keep streaming around while the user retries
    }
  }, [onImageReady, openCamera, stopStream]);

  const upload = useCallback(
    async (file) => {
      if (!file) return;
      setBusy(true);
      setError("");
      try {
        const data = await createScene(file);
        onImageReady({ ...data, image_blob: file });
      } catch (e) {
        setError(String(e.message || e));
      } finally {
        setBusy(false);
      }
    },
    [onImageReady]
  );

  return (
    <section className="screen capture">
      <h1>
        <span className="logo-glow">YAP</span> — bring a room to life
      </h1>
      <p className="lede">
        Photograph a place. Pick what you can see. The objects start talking — and in the
        Conspiracy mode, they keep a secret about <em>you</em>.
      </p>
      <div className="camera-frame">
        <video ref={videoRef} playsInline muted className="preview" />
        <canvas ref={canvasRef} className="hidden" />
        {!ready && <div className="camera-fallback">camera loading…</div>}
      </div>
      <div className="row">
        <button className="btn primary" onClick={capture} disabled={!ready || busy}>
          {busy ? "Reading the room…" : "Capture"}
        </button>
        <label className="btn">
          Upload instead
          <input
            type="file"
            accept="image/*"
            className="visually-hidden"
            onChange={(e) => upload(e.target.files?.[0])}
          />
        </label>
        <button className="btn ghost" onClick={openCamera} disabled={busy}>
          Flip camera
        </button>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}
