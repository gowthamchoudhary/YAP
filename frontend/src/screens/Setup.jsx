import { useState } from "react";
import { listVoices } from "../lib/api.js";

// Colors keyed off the design's bounding-box palette (plant green, toy pink, bottle blue, lamp yellow…)
const BOX_COLORS = ["#5df08a", "#f9b3c2", "#7cc4f8", "#f0b45d", "#c8b7f5", "#9df3c7"];

// Personality trait chips — appended to each character's LLM prompt
// (backend/characters.py apply_user_edits) so they bias the generated
// personality, they are not decorative.
const TRAITS = ["Energetic", "Funny", "Brave", "Sarcastic", "Curious", "Chill", "Wise", "Sassy", "Supportive", "Lazy", "Calm", "Observant", "Witty"];

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
  /* eslint-disable-next-line no-unused-vars */
  onStart, // kept for compatibility; startWithEdits is the real entrypoint
  startWithEdits,
}) {
  const activeMode = modes.find((m) => m.id === modeId);
  const cap = activeMode?.cap || 4;

  const [edits, setEdits] = useState({});
  const [voices, setVoices] = useState([]);
  const [voicesErr, setVoicesErr] = useState("");
  const [openId, setOpenId] = useState(null);
  const [voicesLoaded, setVoicesLoaded] = useState(false);

  const ensureVoices = () => {
    if (voicesLoaded) return;
    setVoicesLoaded(true);
    listVoices()
      .then((v) => setVoices(v.voices || []))
      .catch((e) => setVoicesErr(String(e.message || e)));
  };
  const openEditor = (oid) => {
    ensureVoices();
    setOpenId(openId === oid ? null : oid);
  };

  function toggle(id) {
    setSelected((prev) => {
      if (prev.includes(id)) return prev.filter((x) => x !== id);
      if (prev.length >= cap) return prev; // cap enforcement: hard stop
      return [...prev, id];
    });
  }

  const editFor = (oid) => edits[oid] || { name: "", traits: [], voice_id: "" };
  const setEdit = (oid, patch) =>
    setEdits((prev) => ({ ...prev, [oid]: { ...editFor(oid), ...patch } }));
  const toggleTrait = (oid, trait) => {
    const cur = editFor(oid);
    const has = cur.traits.includes(trait);
    setEdit(oid, {
      traits: has ? cur.traits.filter((t) => t !== trait) : [...cur.traits, trait],
    });
  };

  const start = () => (startWithEdits ? startWithEdits(edits) : onStart());
  const objById = (oid) => sceneData.objects.find((o) => o.id === oid);
  const colorFor = (i) => BOX_COLORS[i % BOX_COLORS.length];

  return (
    <div className="setup">
      <header className="screen-head">
        <button className="back-btn" onClick={onRetake} aria-label="New photo">←</button>
        <div className="cap-counter" aria-live="polite">
          {selected.length}/{cap} selected
        </div>
      </header>

      <h2 className="setup-title">Select your characters</h2>
      <p className="setup-sub">Choose which objects you want to bring to life.</p>

      {/* photo with selectable colored bboxes */}
      <div className="photo-stage">
        {sceneData.image_blob ? (
          <img
            src={URL.createObjectURL(sceneData.image_blob)}
            alt="Your photo"
            className="scene-photo"
          />
        ) : null}
        {sceneData.objects.map((o, i) => {
          const on = selected.includes(o.id);
          const pastCap = !on && selected.length >= cap;
          const color = colorFor(i);
          return (
            <button
              key={o.id}
              className={`obj-box ${on ? "on" : ""} ${pastCap ? "past-cap" : ""}`}
              style={{
                left: `${o.bbox[0] * 100}%`,
                top: `${o.bbox[1] * 100}%`,
                width: `${o.bbox[2] * 100}%`,
                height: `${o.bbox[3] * 100}%`,
                borderColor: color,
                color,
              }}
              onClick={() => toggle(o.id)}
              disabled={pastCap}
              aria-pressed={on}
              title={pastCap ? `cap reached (${cap}) — deselect one first` : `${o.label} — ${o.hint || ""}`}
            >
              <span className="obj-label" style={{ background: color }}>
                {o.label}
              </span>
              <span className="obj-check">✓</span>
            </button>
          );
        })}
      </div>

      {/* detected objects chips grid */}
      <div className="detected-row" role="list" aria-label="Detected objects">
        {sceneData.objects.map((o) => {
          const on = selected.includes(o.id);
          const pastCap = !on && selected.length >= cap;
          return (
            <button
              key={o.id}
              role="listitem"
              className={`detected-card ${on ? "selected" : ""}`}
              onClick={() => toggle(o.id)}
              disabled={pastCap}
            >
              <span className="dc-check">{on ? "✓" : ""}</span>
              <span>{o.label}</span>
            </button>
          );
        })}
      </div>

      {/* mode cards */}
      <div className="mode-cards">
        {modes.map((m) => (
          <button
            key={m.id}
            className={`mode-card ${m.id === modeId ? "on" : ""}`}
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

      {/* personality editor — sticky note cards for selected characters */}
      {selected.length > 0 && (
        <div className="personality-editor">
          <h2>Give them a personality</h2>
          <p>Customize or leave blank — the AI creates one for you.</p>
          {selected.map((oid) => {
            const e = editFor(oid);
            return (
              <div className="persona-card" key={oid}>
                <button className="persona-head" onClick={() => openEditor(oid)}>
                  <span className="persona-label">{objById(oid)?.label || oid}</span>
                  <span className="persona-summary">
                    {e.name || "default name"}
                    {e.traits.length ? ` · ${e.traits.join(", ")}` : ""}
                    {e.voice_id ? ` · ${e.voice_id}` : ""}
                  </span>
                  <span className="persona-caret">{openId === oid ? "▾" : "▸"}</span>
                </button>
                {openId === oid && (
                  <div className="persona-body">
                    <label className="persona-row">
                      <span>Name</span>
                      <input
                        value={e.name}
                        placeholder="(let AI pick)"
                        maxLength={32}
                        onChange={(ev) => setEdit(oid, { name: ev.target.value })}
                      />
                    </label>
                    <div className="persona-row">
                      <span>Personality</span>
                      <div className="trait-chips">
                        {TRAITS.map((t, ti) => (
                          <button
                            type="button"
                            key={t}
                            className={`chip t${ti % 3} ${e.traits.includes(t) ? "on" : ""}`}
                            onClick={() => toggleTrait(oid, t)}
                          >
                            {t}
                          </button>
                        ))}
                      </div>
                    </div>
                    <label className="persona-row">
                      <span>Voice</span>
                      <select
                        value={e.voice_id}
                        onChange={(ev) => setEdit(oid, { voice_id: ev.target.value })}
                      >
                        <option value="">(auto-assign)</option>
                        {voices.map((v) => (
                          <option key={v} value={v}>
                            {v}
                          </option>
                        ))}
                      </select>
                    </label>
                    {voicesErr && <p className="error">{voicesErr}</p>}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      <div className="row-center">
        <button
          className="btn-pill dark start-btn"
          disabled={selected.length === 0}
          onClick={start}
          title={selected.length === 0 ? "Select at least one object" : "Let them speak"}
        >
          {selected.length === 0
            ? "Select characters to continue"
            : `Start the Conversation →`}
        </button>
      </div>
      {selected.length >= cap && (
        <p className="note">cap reached: deselect an object to pick a different one.</p>
      )}
    </div>
  );
}
