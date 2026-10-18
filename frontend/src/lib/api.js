// Backend base for REST + WebSocket.
//
// Resolution order:
//   1. VITE_API_BASE (e.g. http://localhost:8000) — absolute, hits backend directly
//      (CORS on the backend already allows http://localhost:5173).
//   2. VITE_API_URL — legacy name kept working.
//   3. "" — same origin: the Vite dev proxy carries /api and /ws to
//      BACKEND_URL || http://localhost:8000 (see vite.config.js).
export const API_BASE = import.meta.env.VITE_API_BASE || import.meta.env.VITE_API_URL || "";

export async function listModes() {
  const r = await fetch(`${API_BASE}/api/modes`);
  if (!r.ok) throw new Error(`GET /api/modes failed: ${r.status}`);
  return r.json();
}

export async function listVoices() {
  const r = await fetch(`${API_BASE}/api/voices`);
  if (!r.ok) throw new Error(`GET /api/voices failed: ${r.status}`);
  return r.json(); // { voices: [ids] }
}

export async function createScene(blob) {
  const form = new FormData();
  form.append("file", blob, "photo.jpg");
  const r = await fetch(`${API_BASE}/api/scene`, { method: "POST", body: form });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const detail = data.detail || `POST /api/scene failed: ${r.status}`;
    throw new Error(detail);
  }
  return data; // { scene_id, objects }
}

export function audioUrl(url) {
  return `${API_BASE}${url}`;
}

export function wsUrl(path) {
  const base = API_BASE;
  if (!base) {
    // same origin -> use the Vite dev proxy
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${proto}//${window.location.host}${path}`;
  }
  const proto = base.startsWith("https") ? "wss" : "ws";
  return `${proto}://${base.replace(/^https?:\/\//, "")}${path}`;
}
