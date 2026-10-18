import { useEffect, useRef } from "react";

export default function DebugDrawer({ open, events, onClose }) {
  const listRef = useRef(null);
  useEffect(() => {
    if (open && listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [events, open]);
  if (!open) return null;
  return (
    <aside className="debug-drawer" role="dialog" aria-label="Debug drawer">
      <header>
        <span>debug</span>
        <button className="btn-ghost" onClick={onClose} aria-label="Close debug drawer">
          ×
        </button>
      </header>
      <ol ref={listRef}>
        {events.map((e, i) => (
          <li key={i}>
            <code>{e.at}</code> {format(e)}
          </li>
        ))}
      </ol>
    </aside>
  );
}

function DeciderDebug({ decider, style }) {
  if (!decider) return null;
  const scores = decider.scores || {};
  const scoreParts = Object.entries(scores).map(([k, v]) => `${k}=${v}`);
  return (
    <span className="decider-debug">
      <em>decider:</em> chosen=<b>{decider.chosen || "(silence)"}</b> · {decider.reason}
      {scoreParts.length > 0 && <span className="dim"> scores: {scoreParts.join(" ")}</span>}
      {style && <span className="dim"> · style: {style.delivery || "—"} / {style.method}</span>}
    </span>
  );
}

function format(e) {
  if (e.kind === "ws-in") {
    const msg = e.msg || {};
    if (msg.type === "line" && msg.debug) {
      return (
        <span>
          line {msg.line_id} {msg.speaker_name ? <b>{msg.speaker_name}</b> : null}
          <br />
          <DeciderDebug decider={msg.debug.decider} style={msg.debug.style} />
        </span>
      );
    }
    return <span>{JSON.stringify(msg)}</span>;
  }
  if (e.kind === "ws-out") return <span className="dim">↑ {JSON.stringify(e.msg)}</span>;
  if (e.kind === "ended") return <span>session ended: {JSON.stringify(e.summary)}</span>;
  return JSON.stringify(e);
}
