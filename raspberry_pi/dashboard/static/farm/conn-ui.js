// Simulator / real-drone switching, shared by the top-bar chip, Operations and System.
import { api, live } from "./api.js";
import { ICON, confirmDialog, esc, toast } from "./util.js";

const NAME = { sim: "Simulator", real: "Real drone" };

/** Ask, switch, report. Resolves to the new connection info, or null if cancelled / refused. */
export async function switchTo(mode) {
  if (live.state?.local === false) { toast("Switching is only possible from the drone's local Wi-Fi.", "warn"); return null; }
  let info;
  try { info = await api("/api/connection"); } catch (e) { toast(e.message, "error"); return null; }
  if (info.mode === mode) return info;
  if (info.blocked) { toast(info.blocked, "warn", 8000); return null; }
  if (!info.configured[mode]) {
    toast(`No ${mode === "sim" ? "simulator" : "drone"} address is set. Add it under System → Connection.`, "warn", 9000);
    location.hash = "#/system";
    return null;
  }
  const ok = await confirmDialog(mode === "real" ? {
    title: "Switch to the real drone?",
    body: `<p>Mission control will talk to the <strong>real flight controller</strong>${info.urls.real ? ` (<code>${esc(info.urls.real)}</code>)` : ""}.
      Missions you start will <strong>arm the motors and fly</strong>.</p>
      <p style="margin-top:8px">Real flights are stored separately from simulator flights. Launch stays blocked until every safety check passes,
      including the autopilot's own failsafes and geofence.</p>`,
    confirmText: "Use the real drone", kind: "warn",
    requireCheck: "I understand commands from now on control the real drone.",
  } : {
    title: "Switch to the simulator?",
    body: `<p>Mission control will talk to the ArduPilot simulator${info.urls.sim ? ` (<code>${esc(info.urls.sim)}</code>)` : ""}.
      Nothing real will move. Simulator flights are stored separately from real records.</p>`,
    confirmText: "Use the simulator",
  });
  if (!ok) return null;
  try {
    const r = await api("/api/connection", { method: "POST", body: { mode, confirm: true }, timeout: 20000 });
    toast(`Now connected to the ${NAME[mode].toLowerCase()}. Waiting for its heartbeat…`, "ok");
    window.dispatchEvent(new CustomEvent("connectionchange", { detail: r }));
    return r;
  } catch (e) { toast(e.message, "error", 8000); return null; }
}

/** Segmented control + status line. Returns a function that refreshes it from live state. */
export function connectionControl(host) {
  host.innerHTML = `<div class="row" style="justify-content:space-between">
      <div class="seg" role="group" aria-label="Flight controller">
        <button data-mode="sim">${ICON("cpu")}&nbsp;Simulator</button><button data-mode="real">${ICON("drone")}&nbsp;Real drone</button></div>
      <span class="small" data-role="status"></span></div>
    <p class="small muted" data-role="hint" style="margin-top:8px"></p>`;
  host.addEventListener("click", (e) => { const b = e.target.closest("button[data-mode]"); if (b) switchTo(b.dataset.mode); });
  return (s) => {
    const mode = s.mode || (s.sim ? "sim" : "real"), t = s.telemetry || {};
    host.querySelectorAll("button[data-mode]").forEach((b) => b.setAttribute("aria-current", String(b.dataset.mode === mode)));
    host.querySelector('[data-role="status"]').innerHTML = !s.telemetry_enabled
      ? `<span class="tag tag--warn">${ICON("alert")}No address set</span>`
      : t.link ? `<span class="tag tag--ok">${ICON("check")}Connected</span>` : `<span class="tag tag--bad">${ICON("x")}No heartbeat</span>`;
    host.querySelector('[data-role="hint"]').textContent = mode === "sim"
      ? "Simulator: flights are simulated and stored separately. Safety-configuration checks are advisory."
      : "Real drone: commands arm and fly the aircraft. Autopilot failsafes and the geofence must be on before launch.";
  };
}
