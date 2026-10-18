import { useCallback, useEffect, useRef, useState } from "react";
import { createScene } from "../lib/api.js";

export default function CaptureScreen({ onImageReady, onBack }) {
  const videoRef = useRef(null);
  const canvasRef = useRef(null);
  const streamRef = useRef(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [ready, setReady] = useState(false);
  const [facing, setFacing] = useState("environment");

  const stopStream = useCallback(() => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
  }, []);

  const openCamera = useCallback(
    async (facingMode = facing) => {
      setError("");
      try {
        stopStream();
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: facingMode }, width: { ideal: 1280 }, height: { ideal: 720 } },
          audio: false,
        });
        streamRef.current = stream;
        videoRef.current.srcObject = stream;
        await videoRef.current.play();
        setReady(true);
      } catch (e) {
        setReady(false);
        setError("Camera unavailable. Upload a photo instead.");
      }
    },
    [facing, stopStream]
  );

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
      openCamera();
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
    <div className="capture">
      <button className="back-btn" onClick={onBack} aria-label="Back to home">
        ←
      </button>

      <div className="camera-frame">
        <video ref={videoRef} playsInline muted className="camera-video" />
        <canvas ref={canvasRef} className="hidden" />
        {!ready && !busy && <div className="camera-fallback">camera loading…</div>}

        {/* viewfinder corner brackets */}
        <span className="vf-corner vf-tl" aria-hidden />
        <span className="vf-corner vf-tr" aria-hidden />
        <span className="vf-corner vf-bl" aria-hidden />
        <span className="vf-corner vf-br" aria-hidden />

        <p className="vf-hint scribble-script">
          Point your camera <br /> at a scene <span className="vf-arrow">→</span>
        </p>

        {!busy && (
          <div className="camera-toolbar">
            <span className="mode-pill active">PHOTO</span>
            <span className="mode-pill">VIDEO</span>
            <span className="mode-pill">IMPORT</span>
          </div>
        )}
      </div>

      {/* shutter row */}
      <div className="shutter-row">
        <label className="gallery-btn">
          <input
            type="file"
            accept="image/*"
            className="visually-hidden"
            onChange={(e) => upload(e.target.files?.[0])}
          />
          <span role="img" aria-label="Upload a photo">🖼</span>
        </label>
        <button
          className="shutter"
          onClick={capture}
          disabled={!ready || busy}
          aria-label="Capture photo"
        />
        <button
          className="flip-btn"
          onClick={() => {
            const next = facing === "environment" ? "user" : "environment";
            setFacing(next);
            openCamera(next);
          }}
          disabled={busy}
          aria-label="Flip camera"
        >
          ↻
        </button>
      </div>

      {/* "Finding objects" overlay */}
      {busy && (
        <div className="finding-overlay">
          <div className="finding-blob" aria-hidden>👀</div>
          <div className="finding-card">
            <strong>Finding objects…</strong>
            <div className="finding-bar">
              <div className="finding-bar-fill" />
            </div>
            <span className="dim">Detecting objects in your scene</span>
          </div>
        </div>
      )}

      {error && <p className="error" role="alert">{error}</p>}
    </div>
  );
}
