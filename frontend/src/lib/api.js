// Backend base: vite dev proxy handles /api in development; override with VITE_API_URL
// if the backend lives elsewhere (e.g. a phone on the LAN: http://192.168.x.x:8000).
export const API_BASE = import.meta.env.VITE_API_URL || "";

export async function listModes() {
  const r = await fetch(`${API_BASE}/api/modes`);
  if (!r.ok) throw new Error(`GET /api/modes failed: ${r.status}`);
  return r.json();
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
  const base = `${API_BASE}` || window.location.origin;
  const proto = base.startsWith("https") ? "wss" : "ws";
  return `${proto}://${base.replace(/^https?:\/\//, "")}${path}`;
}
