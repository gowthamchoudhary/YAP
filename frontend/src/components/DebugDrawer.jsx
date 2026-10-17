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

function format(e) {
  if (e.kind === "ws-in") return <span>{JSON.stringify(e.msg)}</span>;
  if (e.kind === "ws-out") return <span className="dim">↑ {JSON.stringify(e.msg)}</span>;
  if (e.kind === "ended") return <span>session ended: {JSON.stringify(e.summary)}</span>;
  return JSON.stringify(e);
}
