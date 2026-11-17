// Backend access: JSON requests with readable errors, and the live SSE stream with
// automatic reconnection and stale-data detection.

/**
 * @typedef {{ok:boolean, stage:"ack"|"confirm"|"timeout"|"error", detail:string, command?:string, result?:number}} CommandResult
 * @typedef {{id:string, name:string, crop:string, polygon:number[][], area_m2:number, archived:boolean}} Field
 * @typedef {{id:string, t:number, label:string, kind:string, p:number|null, status:string, lat:number|null, lon:number|null,
 *           h_acc_m:number|null, mission_id:string|null, field_id:string|null, image:string|null, thumb:string|null,
 *           n_leaves:number, review_status:string}} Detection
 */

export class ApiError extends Error {
  constructor(message, status, data) { super(message); this.status = status; this.data = data; }
}

export async function api(path, { method = "GET", body, timeout = 30000 } = {}) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeout);
  let res;
  try {
    res = await fetch(path, {
      method, signal: ctl.signal,
      headers: body !== undefined ? { "Content-Type": "application/json" } : {},
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    throw new ApiError(e.name === "AbortError" ? "The server took too long to answer." : "Cannot reach the console. Check the connection.", 0);
  } finally {
    clearTimeout(timer);
  }
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON */ }
  if (!res.ok) throw new ApiError((data && data.error) || `Request failed (${res.status}).`, res.status, data);
  return data;
}

// ---------------------------------------------------------------- live stream
const STALE_AFTER_MS = 3000;
export const live = {
  state: null, lastAt: 0, status: "connecting", listeners: new Set(), statusListeners: new Set(),
  subscribe(fn) { this.listeners.add(fn); if (this.state) fn(this.state); return () => this.listeners.delete(fn); },
  onStatus(fn) { this.statusListeners.add(fn); fn(this.status); return () => this.statusListeners.delete(fn); },
  get age() { return this.lastAt ? Date.now() - this.lastAt : Infinity; },
  get stale() { return this.age > STALE_AFTER_MS; },
};

function setStatus(s) {
  if (live.status === s) return;
  live.status = s;
  live.statusListeners.forEach((fn) => fn(s));
}

let es = null, retry = 1000;
export function connect() {
  if (es) es.close();
  es = new EventSource("/events");
  es.onmessage = (ev) => {
    try { live.state = JSON.parse(ev.data); } catch { return; }
    live.lastAt = Date.now();
    retry = 1000;
    setStatus("live");
    live.listeners.forEach((fn) => { try { fn(live.state); } catch (e) { console.error(e); } });
  };
  es.onerror = () => {
    es.close();
    setStatus(live.lastAt ? "reconnecting" : "offline");
    setTimeout(connect, retry);
    retry = Math.min(retry * 2, 15000);
  };
}
setInterval(() => {
  if (live.status === "live" && live.stale) setStatus("stale");
}, 1000);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && (live.stale || live.status !== "live")) connect();
});
