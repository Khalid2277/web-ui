// System status: flight controller link, GPS/EKF, Raspberry Pi, camera, AI model, sensors, database, settings.
import { api, live } from "../api.js";
import { connectionControl } from "../conn-ui.js";
import { metaRow, ICON, ago, clock, dur, esc, fmt, int, pct, toast } from "../util.js";

const FIX = ["No GPS", "No fix", "2D fix", "3D fix", "DGPS", "RTK float", "RTK fixed"];

export async function mount(root) {
  root.innerHTML = `
    <div class="page-head"><div><h1>System status</h1>${metaRow()}
      <p>Health of every part of the inspection system, and the settings that drive planning and pre-flight checks.</p></div></div>
    <div class="grid cols-12">
      <section class="card span-6"><div class="card__head"><h2>${ICON("drone")}Flight controller</h2><span id="fc-tag"></span></div><dl class="kv" id="fc"></dl></section>
      <section class="card span-6"><div class="card__head"><h2>${ICON("satellite")}Navigation</h2></div><dl class="kv" id="nav"></dl></section>
      <section class="card span-6"><div class="card__head"><h2>${ICON("cpu")}Raspberry Pi</h2></div><dl class="kv" id="pi"></dl></section>
      <section class="card span-6"><div class="card__head"><h2>${ICON("camera")}Camera &amp; AI model</h2></div><dl class="kv" id="cam"></dl></section>
      <section class="card span-6"><div class="card__head"><h2>${ICON("grid")}Data</h2></div><dl class="kv" id="db"></dl></section>
      <section class="card span-6"><div class="card__head"><h2>${ICON("history")}Autopilot messages</h2></div><ul class="cmd-log" id="msgs"></ul></section>
      <section class="card span-12"><div class="card__head"><h2>${ICON("signal")}Connection<span class="card__sub">Which flight controller mission control talks to</span></h2></div>
        <div id="conn-ctl"></div>
        <form id="conn-form" class="form-row" style="margin-top:14px;align-items:end">
          <label class="field">Simulator address<input name="sim_url" placeholder="tcp:192.168.1.20:5763" autocomplete="off" spellcheck="false"><small class="muted">ArduPilot SITL, e.g. the Mac's simulator on port 5763.</small></label>
          <label class="field">Real drone address<input name="real_url" placeholder="/dev/ttyAMA0" autocomplete="off" spellcheck="false"><small class="muted">On the Pi the Pixhawk is on <code>/dev/ttyAMA0</code> (57600 baud).</small></label>
          <div><button class="btn">Save addresses</button></div></form></section>
      <section class="card span-12"><div class="card__head"><h2>${ICON("shield")}Settings</h2><span class="small muted" id="set-note"></span></div><form id="settings"></form></section>
    </div>`;
  const $ = (s) => root.querySelector(s);
  const kv = (rows) => rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join("");
  let sys = null;

  async function load() {
    try { sys = await api("/api/system"); } catch (e) { toast(e.message, "error"); return; }
    const m = sys.model || {}, pi = sys.pi || {}, d = sys.database;
    $("#pi").innerHTML = pi.t ? kv([
      ["Host", esc(pi.hostname || "—")], ["IP address", esc(pi.ip || "—")], ["CPU temperature", pi.cpu_temp != null ? `${fmt(pi.cpu_temp, 1)} °C` : "—"],
      ["Load (1 min)", `${fmt(pi.load, 2)} on ${pi.cores} cores`], ["Memory used", pi.mem_pct != null ? `${pi.mem_pct}%` : "—"], ["Disk used", `${pi.disk_pct}%`],
      ["Uptime", pi.uptime_s ? dur(pi.uptime_s) : "—"],
      ["Power", pi.undervoltage_now ? '<span class="tag tag--bad">Under-voltage now</span>' : pi.undervoltage_since_boot ? '<span class="tag tag--warn">Under-voltage since boot</span>' : '<span class="tag tag--ok">OK</span>'],
      ["Wi-Fi", pi.wifi_ssid ? `${esc(pi.wifi_ssid)} · ${pi.wifi_signal}%` : "—"],
      ["Public link", pi.public_url ? `<a href="${esc(pi.public_url)}" target="_blank" rel="noopener">${esc(pi.public_url)}</a> (view-only)` : "Off"],
    ]) : "<dd>Collecting…</dd>";
    $("#cam").innerHTML = kv([
      ["Camera", esc(sys.camera.status)], ["Frame rate", `${fmt(sys.camera.fps, 1)} fps`], ["Last frame", sys.camera.frame_age_s != null ? `${fmt(sys.camera.frame_age_s, 1)} s ago` : "never"],
      ["Model", esc(`${m.name || "—"} ${m.version || ""}`)], ["Detector", esc(m.detector || "—")], ["Classifier", esc(m.architecture || "—")],
      ["Field accuracy", m.field_accuracy != null ? pct(m.field_accuracy, 1) : "—"], ["False disease rate", m.false_disease_rate != null ? pct(m.false_disease_rate, 1) : "—"],
      ["Lab-only classes", esc((m.lab_only_classes || []).join(", ") || "none")],
    ]);
    $("#db").innerHTML = kv([
      ["Detections", int(d.detections)], ["Missions", int(d.missions)], ["Last detection", d.last_detection_t ? esc(ago(d.last_detection_t)) : "never"],
      ["Database size", `${fmt(d.bytes / 1048576, 1)} MB`], ["Field sensors", sys.sensors.readings ? `${sys.sensors.readings} readings, last ${esc(ago(sys.sensors.last_t))}` : "No sensor nodes reporting"],
      ["MAVLink", esc(sys.mavlink_url)], ["Mode", sys.sim ? '<span class="tag tag--info">Simulator (SITL)</span>' : sys.dev ? '<span class="tag tag--warn">Developer mode</span>' : "Live drone"],
      ["This browser", sys.local ? '<span class="tag tag--ok">Local network — commands allowed</span>' : '<span class="tag tag--warn">Public link — view only</span>'],
    ]);
  }

  function onState(s) {
    const t = s.telemetry || {};
    $("#fc-tag").innerHTML = `<span class="tag ${t.link ? "tag--ok" : "tag--bad"}">${t.link ? "Connected" : "No link"}</span>`;
    $("#fc").innerHTML = s.telemetry_enabled ? kv([
      ["Heartbeat", t.heartbeat_age_s != null ? `${fmt(t.heartbeat_age_s, 1)} s ago` : "never"], ["Mode", esc(t.mode || "—")],
      ["Armed", t.link ? (t.armed ? '<span class="tag tag--warn">Armed</span>' : "Disarmed") : "—"],
      ["Pre-arm checks", t.prearm_ok == null ? "—" : t.prearm_ok ? '<span class="tag tag--ok">Passing</span>' : '<span class="tag tag--bad">Failing</span>'],
      ["Battery", t.battery_v != null ? `${fmt(t.battery_v, 2)} V${t.battery_a != null ? ` · ${fmt(t.battery_a, 1)} A` : ""}${t.battery_pct != null ? ` · ${t.battery_pct}%` : ""}` : "—"],
      ["RC transmitter", t.rc_ok ? `Linked${t.rc_rssi != null && t.rc_rssi !== 255 ? ` · RSSI ${t.rc_rssi}` : ""}` : "Not detected"],
      ["Link error", esc(t.link_error || "none")],
    ]) : "<dt>Status</dt><dd>Telemetry disabled (developer mode without MAVLINK_URL)</dd>";
    $("#nav").innerHTML = kv([
      ["GPS", `${FIX[t.gps_fix ?? 0] || t.gps_fix} · ${t.gps_sats ?? 0} satellites`],
      ["Accuracy", t.gps_h_acc_m != null ? `± ${fmt(t.gps_h_acc_m, 1)} m` : t.gps_hdop != null ? `HDOP ${fmt(t.gps_hdop, 2)} (≈ ± ${fmt(t.gps_hdop * 2.5, 1)} m)` : "—"],
      ["EKF position", t.ekf_pos_ok == null ? "No EKF status yet" : t.ekf_pos_ok ? '<span class="tag tag--ok">Valid</span>' : '<span class="tag tag--bad">Not valid</span>'],
      ["Home", t.home ? `${t.home.lat.toFixed(6)}, ${t.home.lon.toFixed(6)}` : "Not set"],
      ["Position", t.lat != null ? `${t.lat.toFixed(6)}, ${t.lon.toFixed(6)}` : "—"], ["Altitude", t.alt_rel != null ? `${fmt(t.alt_rel, 1)} m above home` : "—"],
    ]);
    $("#msgs").innerHTML = (t.messages || []).map((m) => `<li data-level="${m.severity <= 3 ? "error" : m.severity <= 4 ? "warn" : ""}"><time>${clock(m.t)}</time><span>${esc(m.text)}</span></li>`).join("")
      || "<li><span></span><span class='muted'>No messages received.</span></li>";
  }

  // ---------------------------------------------------------------- settings
  const FIELDS = [
    ["farm_name", "Farm name", "text"], ["endurance_min", "Usable flight time (min)", "number", 1, 60, 0.5, "Battery estimates use this. Measure it on a real flight."],
    ["min_battery_v", "Minimum battery to launch (V)", "number", 6, 30, 0.1], ["camera_hfov_deg", "Camera horizontal field of view (°)", "number", 10, 150, 1, "Sets the swath and line spacing."],
    ["default_altitude_m", "Default altitude (m)", "number", 3, 60, 1], ["default_speed_ms", "Default speed (m/s)", "number", 1, 10, 0.5],
    ["sidelap", "Side-lap between lines (0–0.8)", "number", 0, 0.8, 0.05], ["max_hdop", "Maximum GPS HDOP to launch", "number", 0.5, 5, 0.1], ["min_sats", "Minimum GPS satellites", "number", 4, 20, 1],
  ];
  async function loadSettings() {
    const st = await api("/api/settings");
    const local = live.state?.local !== false;
    $("#settings").innerHTML = `<div class="form-row">${FIELDS.map(([k, label, type, min, max, step, hint]) => `<label class="field">${esc(label)}
        <input name="${k}" type="${type}" value="${esc(st[k])}" ${min != null ? `min="${min}" max="${max}" step="${step}"` : 'maxlength="60"'} ${local ? "" : "disabled"}>${hint ? `<small class="muted">${esc(hint)}</small>` : ""}</label>`).join("")}</div>
      <div class="row" style="margin-top:12px"><button class="btn" ${local ? "" : "disabled"}>Save settings</button></div>`;
    $("#set-note").textContent = local ? "" : "Settings can only be changed from the local network.";
  }
  $("#settings").addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = Object.fromEntries(new FormData(e.target));
    for (const k in body) if (k !== "farm_name") body[k] = Number(body[k]);
    try {
      const s = await api("/api/settings", { method: "PUT", body });
      toast("Settings saved.", "ok");
      window.dispatchEvent(new CustomEvent("farmname", { detail: s.farm_name }));
    } catch (err) { toast(err.message, "error"); }
  });

  const updateConn = connectionControl($("#conn-ctl"));
  async function loadConn() {
    const c = await api("/api/connection").catch(() => null);
    if (!c) return;
    const f = $("#conn-form"), local = live.state?.local !== false;
    f.sim_url.value = c.urls.sim || ""; f.real_url.value = c.urls.real || "";
    f.querySelectorAll("input,button").forEach((el) => { el.disabled = !local; });
  }
  $("#conn-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    try {
      await api("/api/connection", { method: "PUT", body: { sim_url: f.sim_url.value.trim(), real_url: f.real_url.value.trim() } });
      toast("Connection addresses saved.", "ok");
      loadConn();
    } catch (err) { toast(err.message, "error", 8000); }
  });

  await Promise.all([load(), loadSettings(), loadConn()]);
  const unsub = live.subscribe((s) => { onState(s); updateConn(s); });
  const timer = setInterval(load, 10000);
  return () => { unsub(); clearInterval(timer); };
}
