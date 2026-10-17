import { useState } from "react";

const ACCENT = { conspiracy: "#5df08a", judges: "#f0b45d" };

export default function SetupScreen({
  sceneData,
  modes,
  modeId,
  setModeId,
  difficulty,
  setDifficulty,
  selected,
  setSelected,
  onRetake,
  onStart,
}) {
  const activeMode = modes.find((m) => m.id === modeId);
  const cap = activeMode?.cap || 4;

  function toggle(id) {
    setSelected((prev) => {
      if (prev.includes(id)) return prev.filter((x) => x !== id);
      if (prev.length >= cap) return prev; // live cap counter: hard stop
      return [...prev, id];
    });
  }

  return (
    <section className="screen setup">
      <header className="screen-head">
        <button className="btn ghost" onClick={onRetake}>
          ← New photo
        </button>
        <div className="cap-counter" aria-live="polite">
          selected {selected.length} / cap {cap}
        </div>
      </header>

      <div className="photo-stage">
        {sceneData.image_blob ? (
          <img
            src={URL.createObjectURL(sceneData.image_blob)}
            alt="Your photo"
            className="scene-photo"
          />
        ) : null}
        {sceneData.objects.map((o) => {
          const on = selected.includes(o.id);
          return (
            <button
              key={o.id}
              className={`obj-box ${on ? "on" : ""}`}
              style={{
                left: `${o.bbox[0] * 100}%`,
                top: `${o.bbox[1] * 100}%`,
                width: `${o.bbox[2] * 100}%`,
                height: `${o.bbox[3] * 100}%`,
              }}
              onClick={() => toggle(o.id)}
              aria-pressed={on}
              title={`${o.label} — ${o.hint || ""}`}
            >
              <span className="obj-label">
                {o.label}
                <small>{o.id}</small>
              </span>
            </button>
          );
        })}
      </div>

      <div className="mode-cards">
        {modes.map((m) => (
          <button
            key={m.id}
            className={`mode-card ${m.id === modeId ? "on" : ""}`}
            style={{ ["--accent"]: ACCENT[m.id] || "#8bd" }}
            onClick={() => {
              setModeId(m.id);
              setSelected((prev) => prev.slice(0, m.cap));
            }}
          >
            <strong>{m.name}</strong>
            <span>cap up to {m.cap} characters</span>
            <p>{m.tone_short || ""}</p>
          </button>
        ))}
      </div>

      {modeId === "judges" && (
        <div className="difficulty">
          <span>difficulty:</span>
          {["gentle", "normal", "harsh"].map((d) => (
            <button
              key={d}
              className={`chip ${difficulty === d ? "on" : ""}`}
              onClick={() => setDifficulty(d)}
            >
              {d}
            </button>
          ))}
        </div>
      )}

      <div className="row center">
        <button
          className="btn primary big"
          disabled={selected.length === 0}
          onClick={onStart}
          title={selected.length === 0 ? "Select at least one object" : "Let them speak"}
        >
          {activeMode?.name ? `Start — ${activeMode.name}` : "Start"}
        </button>
      </div>
      {selected.length >= cap && (
        <p className="note">cap reached: deselect an object to pick a different one.</p>
      )}
    </section>
  );
}
