// Live operations: camera + AI, live map, mission planning / upload / preflight / launch, in-flight commands.
import { api, live } from "../api.js";
import { connectionControl } from "../conn-ui.js";
import { DEFAULT_CENTER, detectionMarker, droneIcon, fieldStyle, fitTo, homeIcon, makeMap } from "../mapkit.js";
import { metaRow, ICON, clock, confirmDialog, dur, esc, fmt, kindOf, kindTag, labelName, pct, toast } from "../util.js";

const L = window.L;
const ACK_TEXT = { ack: "Acknowledged", confirm: "Confirmed by telemetry", timeout: "No answer (timeout)", error: "Not sent" };

export async function mount(root) {
  root.innerHTML = `
    <div class="page-head">
      <div><h1>Mission control</h1>${metaRow()}
        <p>Plan an inspection, upload it, pass the pre-flight checks and launch. Every command waits for the flight controller's acknowledgement.</p></div>
      <div class="row" id="head-tags"></div>
    </div>
    <div class="grid cols-12">
      <div class="span-7 stack">
        <section class="card card--flush" aria-label="Camera">
          <div class="feed" id="feed"><img id="feed-img" alt="Live camera feed from the drone" hidden><div id="boxes"></div>
            <div class="feed__off" id="feed-off">${ICON("camera", "icon icon--lg")}<span>Camera offline</span></div>
            <div class="feed__bar"><span id="ai-line" class="small">AI: waiting for frames…</span><span class="spacer"></span>
              <button class="btn btn--ghost btn--sm" id="snap">${ICON("camera")}Snapshot &amp; log</button></div></div>
        </section>
        <section class="card" aria-labelledby="lm-h">
          <div class="card__head"><h2 id="lm-h">${ICON("map")}Live map</h2>
            <div class="row small muted"><label class="check" style="min-height:32px"><input type="checkbox" id="follow" checked>Follow drone</label></div></div>
          <div class="map" id="map"></div>
          <div class="map-legend"><span><span class="kind__mark" style="background:var(--accent);border-radius:50%"></span> Drone</span>
            <span><i style="display:inline-block;width:18px;border-top:2px dashed #f4f1e6;outline:3px solid #2a3a2f;background:#2a3a2f;vertical-align:middle"></i> Planned route</span>
            <span><i style="display:inline-block;width:18px;border-top:3px solid var(--accent);vertical-align:middle"></i> Flown track</span></div>
        </section>
      </div>
      <div class="span-5 stack">
        <section class="card" aria-labelledby="cn-h"><div class="card__head"><h2 id="cn-h">${ICON("signal")}Flight controller</h2></div>
          <div id="conn-ctl"></div></section>
        <section class="card" aria-labelledby="mc-h"><div class="card__head"><h2 id="mc-h">${ICON("route")}Inspection mission</h2><span id="mc-tag"></span></div>
          <div id="mission-panel"></div></section>
        <section class="card" aria-labelledby="tl-h"><div class="card__head"><h2 id="tl-h">${ICON("signal")}Telemetry</h2><span class="small muted" id="tel-age"></span></div>
          <div class="tiles" id="tiles"></div>
          <div class="btn-grid" id="safety" style="margin-top:12px"></div></section>
        <section class="card" aria-labelledby="cl-h"><div class="card__head"><h2 id="cl-h">${ICON("history")}Command &amp; event log</h2></div>
          <ul class="cmd-log" id="cmd-log"></ul></section>
        <section class="card" aria-labelledby="ld-h"><div class="card__head"><h2 id="ld-h">${ICON("leaf")}Live detections</h2></div>
          <ul class="det-list" id="live-dets"></ul></section>
      </div>
    </div>`;
  const $ = (s) => root.querySelector(s);

  // ---------------------------------------------------------------- map
  const map = makeMap($("#map"), { zoom: 18 });
  const routeLayer = L.layerGroup().addTo(map), trackLine = L.polyline([], { color: "#c8f169", weight: 3 }).addTo(map);
  const fieldLayer = L.layerGroup().addTo(map), detLayer = L.layerGroup().addTo(map);
  let droneMarker = null, homeMarker = null, centred = false;
  let fields = [], previewPlan = null, activeId = null, loadedTrackFor = null;

  function drawRoute(waypoints) {
    routeLayer.clearLayers();
    if (!waypoints?.length) return;
    L.polyline(waypoints, { color: "#f4f1e6", weight: 2, dashArray: "6 6", opacity: 0.9 }).addTo(routeLayer);
    L.circleMarker(waypoints[0], { radius: 6, color: "#f4f1e6", fillColor: "#c8f169", fillOpacity: 1, weight: 2 }).bindTooltip("First waypoint").addTo(routeLayer);
    L.circleMarker(waypoints.at(-1), { radius: 5, color: "#f4f1e6", fillColor: "#0c120e", fillOpacity: 1, weight: 2 }).bindTooltip("Last waypoint, then return to launch").addTo(routeLayer);
  }
  function drawFields(selectedId) {
    fieldLayer.clearLayers();
    fields.forEach((f) => L.polygon(f.polygon, fieldStyle(f.id === selectedId)).bindTooltip(f.name).addTo(fieldLayer));
  }

  // ---------------------------------------------------------------- camera + AI
  let streamSet = false;
  function renderCamera(s) {
    const camOk = s.camera?.status === "live";
    if (camOk && !streamSet) { $("#feed-img").src = `/stream.mjpg?fps=${s.local === false ? 4 : 12}`; streamSet = true; }
    $("#feed-img").hidden = !camOk;
    $("#feed-off").hidden = camOk;
    $("#feed-off").querySelector("span").textContent = camOk ? "" : `Camera ${s.camera?.status || "offline"}`;
    const ai = s.ai;
    if (!ai) { $("#ai-line").textContent = camOk ? "AI: analysing…" : "AI: no camera frames"; $("#boxes").innerHTML = ""; return; }
    const h = ai.headline;
    $("#ai-line").innerHTML = `AI · ${ai.leaves.length} lea${ai.leaves.length === 1 ? "f" : "ves"} · ${kindTag(h.label, h.status)} <span class="mono muted">${pct(h.p)}</span> <span class="muted">· ${ai.ms} ms</span>`;
    $("#boxes").innerHTML = ai.leaves.filter((l) => l.box).map((l, i) => {
      const [x0, y0, x1, y1] = l.box, k = kindOf(l.label, l.status);
      return `<div class="box" data-kind="${k}" style="left:${x0 * 100}%;top:${y0 * 100}%;width:${(x1 - x0) * 100}%;height:${(y1 - y0) * 100}%">
        <span>${i + 1} · ${k === "uncertain" ? "?" : esc(labelName(l.label))} ${pct(l.p)}</span></div>`;
    }).join("");
  }
  $("#snap").addEventListener("click", async (e) => {
    const b = e.currentTarget; b.disabled = true;
    try {
      const d = await api("/api/snapshot", { method: "POST", body: {} });
      toast(`Logged ${labelName(d.label)} (${pct(d.p)})${d.lat == null ? ", without GPS position" : ""}.`, "ok");
      addLiveDet(d);
    } catch (err) { toast(err.message, "error"); } finally { b.disabled = false; }
  });

  // ---------------------------------------------------------------- telemetry tiles + safety buttons
  let safetyKey = "";
  function renderTelemetry(s) {
    const t = s.telemetry || {}, stale = !t.link || live.stale;
    const fix = ["No GPS", "No fix", "2D", "3D", "DGPS", "RTK float", "RTK"][t.gps_fix ?? 0] || "—";
    const tiles = [
      ["altitude", "Altitude", t.alt_rel != null ? `${fmt(t.alt_rel, 1)} m` : "—", "above home"],
      ["speed", "Ground speed", t.groundspeed != null ? `${fmt(t.groundspeed, 1)} m/s` : "—", t.climb != null ? `climb ${fmt(t.climb, 1)} m/s` : ""],
      ["compass", "Heading", t.heading != null ? `${fmt(t.heading, 0)}°` : "—", ""],
      ["battery", "Battery", t.battery_v != null ? `${fmt(t.battery_v, 2)} V` : "—", t.battery_pct != null ? `${t.battery_pct}% remaining` : "% not reported"],
      ["satellite", "GPS", `${fix} · ${t.gps_sats ?? 0}`, t.gps_hdop != null ? `HDOP ${fmt(t.gps_hdop, 1)}${t.gps_h_acc_m != null ? ` · ±${fmt(t.gps_h_acc_m, 1)} m` : ""}` : ""],
      ["drone", "Mode", t.mode || "—", t.link ? (t.armed ? "ARMED" : "disarmed") : "no link"],
      ["pin", "Position", t.lat != null ? `${t.lat.toFixed(5)}` : "—", t.lon != null ? t.lon.toFixed(5) : ""],
      ["signal", "Telemetry", t.heartbeat_age_s != null ? `${fmt(t.heartbeat_age_s, 1)} s` : "—", t.link ? "since heartbeat" : (t.link_error || "no heartbeat")],
    ];
    $("#tiles").innerHTML = tiles.map(([i, label, v, sub]) => `<div class="tile" data-stale="${stale && label !== "Telemetry"}"><b>${ICON(i)}${label}</b><span>${esc(v)}</span><small>${esc(sub)}</small></div>`).join("");
    $("#tel-age").textContent = t.link ? `Live · ${clock(s.t)}` : s.telemetry_enabled ? "No link to the flight controller" : "Telemetry disabled";
    // Return-to-launch / land are available whenever the drone is armed, with or without a mission.
    const can = s.local !== false && t.link && t.armed;
    const m = s.mission?.active, paused = m?.status === "paused" || ((t.mode === "BRAKE" || t.mode === "LOITER") && m);
    const key = `${can}|${!!m}|${paused}|${s.local}|${t.link}|${t.armed}`;
    if (key === safetyKey) return;
    safetyKey = key;
    $("#safety").innerHTML = `
      ${paused ? `<button class="btn" data-cmd="resume" ${can ? "" : "disabled"}>${ICON("play")}Resume mission</button>`
        : `<button class="btn btn--ghost" data-cmd="pause" ${can && m ? "" : "disabled"}>${ICON("pause")}Pause (hold)</button>`}
      <button class="btn btn--warn" data-cmd="rtl" ${can ? "" : "disabled"}>${ICON("home")}Return to launch</button>
      <button class="btn btn--danger" data-cmd="land" style="grid-column:span 2" ${can ? "" : "disabled"}>${ICON("land")}Land now (where it is)</button>
      <p class="small muted" style="grid-column:span 2">${s.local === false ? "Commands are disabled on the public link." : !t.link ? "No link to the flight controller." : !t.armed ? "Flight commands become available once the drone is armed." : "Each command asks for confirmation, then waits for the autopilot's answer."}</p>`;
  }
  const CMD = {
    pause: { title: "Pause the mission?", body: "The drone stops and holds its position (BRAKE mode, or LOITER if BRAKE is refused). Resume continues from the current waypoint.", kind: "", ok: "Pause" },
    resume: { title: "Resume the mission?", body: "The drone switches back to AUTO and continues the survey from the current waypoint.", kind: "", ok: "Resume" },
    rtl: { title: "Return to launch?", body: "The drone climbs to its return altitude, flies back to the launch point and lands. The survey will not continue.", kind: "warn", ok: "Return to launch" },
    land: { title: "Land immediately?", body: "<strong>The drone descends and lands where it is now</strong> — make sure the area below is clear. Use this only if returning is unsafe.", kind: "danger", ok: "Land now" },
  };
  $("#safety").addEventListener("click", async (e) => {
    const b = e.target.closest("button[data-cmd]");
    if (!b || b.disabled) return;
    const c = CMD[b.dataset.cmd];
    if (!(await confirmDialog({ title: c.title, body: `<p>${c.body}</p>`, confirmText: c.ok, kind: c.kind }))) return;
    b.disabled = true;
    try {
      const r = await api(`/api/mission/${b.dataset.cmd}`, { method: "POST", body: { confirm: true } });
      toast(`${c.ok}: ${ACK_TEXT[r.stage]} — ${r.detail}`, "ok");
    } catch (err) {
      const d = err.data || {};
      toast(`${c.ok} failed: ${d.stage ? ACK_TEXT[d.stage] + " — " : ""}${d.detail || err.message}`, "error", 9000);
    }
  });

  // ---------------------------------------------------------------- mission panel
  const panel = $("#mission-panel");
  let preflightTimer = null, preflightState = null, current = null, lastActiveId;

  async function loadFields() {
    try { fields = await api("/api/fields"); } catch { fields = []; }
  }

  function planForm() {
    stopPreflight();
    current = null;
    if (!fields.length) {
      panel.innerHTML = `<div class="empty">${ICON("map", "icon icon--lg")}<strong>No fields yet</strong><span>Draw a field boundary on the Farm map first.</span>
        <a class="btn" href="#/map?draw=1">${ICON("plus")}Draw a field</a></div>`;
      return;
    }
    panel.innerHTML = `
      <div class="form-row">
        <label class="field" style="grid-column:1/-1">Field<select id="f-field">${fields.map((f) => `<option value="${esc(f.id)}">${esc(f.name)} · ${(f.area_m2 / 10000).toFixed(2)} ha</option>`).join("")}</select></label>
        <label class="field">Altitude (m)<input id="f-alt" type="number" min="3" max="60" step="1" inputmode="decimal"></label>
        <label class="field">Speed (m/s)<input id="f-speed" type="number" min="1" max="10" step="0.5" inputmode="decimal"></label>
      </div>
      <div class="row" style="margin-top:12px"><button class="btn btn--ghost" id="b-preview">${ICON("route")}Preview route</button></div>
      <div id="plan-out" style="margin-top:12px"></div>
      <div id="uploaded"></div>`;
    api("/api/settings").then((st) => { $("#f-alt").value = st.default_altitude_m; $("#f-speed").value = st.default_speed_ms; });
    const sel = $("#f-field");
    sel.addEventListener("change", () => { previewPlan = null; $("#plan-out").innerHTML = ""; routeLayer.clearLayers(); focusField(); });
    $("#b-preview").addEventListener("click", preview);
    focusField();
    showUploaded();
  }
  function focusField() {
    const f = fields.find((x) => x.id === $("#f-field")?.value);
    drawFields(f?.id);
    if (f && !activeId) fitTo(map, f.polygon);
  }
  async function showUploaded() {
    const ms = await api("/api/missions?limit=20").catch(() => []);
    const up = ms.find((m) => m.status === "uploaded");
    const el = $("#uploaded");
    if (!el || !up) return;
    el.innerHTML = `<hr class="rule"><p class="small"><span class="tag tag--ok">${ICON("check")}On the drone</span> ${esc(up.id)} · ${esc(up.field_name || "")} — uploaded and verified.</p>
      <div class="row" style="margin-top:8px"><button class="btn" id="b-use">${ICON("shield")}Run pre-flight checks</button></div>`;
    $("#b-use").addEventListener("click", async () => {
      const md = await api(`/api/missions/${encodeURIComponent(up.id)}`);
      drawRoute(md.plan.waypoints);
      current = md;
      showPreflight();
    });
  }
  async function preview() {
    const body = { field_id: $("#f-field").value, altitude_m: +$("#f-alt").value, speed_ms: +$("#f-speed").value };
    const out = $("#plan-out");
    out.innerHTML = `<div class="skeleton" style="height:80px"></div>`;
    try {
      previewPlan = { ...(await api("/api/missions/plan", { method: "POST", body })), req: body };
    } catch (e) { out.innerHTML = `<p class="tag tag--bad">${esc(e.message)}</p>`; return; }
    const p = previewPlan;
    drawRoute(p.waypoints);
    fitTo(map, p.waypoints);
    const over = p.est_battery_pct > 75;
    out.innerHTML = `<dl class="kv small">
        <dt>Survey lines</dt><dd>${p.lines} · ${fmt(p.line_spacing_m, 1)} m apart</dd>
        <dt>Route length</dt><dd>${fmt(p.total_length_m, 0)} m</dd>
        <dt>Estimated time</dt><dd>${dur(p.est_duration_s)}</dd>
        <dt>Estimated battery</dt><dd style="color:${over ? "var(--bad)" : "inherit"}">${fmt(p.est_battery_pct, 0)}% of usable flight time${over ? " — too long, split the field or fly faster/higher" : ""}</dd>
        <dt>Camera swath</dt><dd>${fmt(p.swath_m, 1)} m</dd>
        <dt>Mission items</dt><dd>${p.n_items}</dd></dl>
      <details class="table-view"><summary>Assumptions behind these estimates</summary><ul class="small muted">${p.assumptions.map((a) => `<li>${esc(a)}</li>`).join("")}</ul></details>
      <div class="row" style="margin-top:12px"><button class="btn" id="b-upload" ${live.state?.local === false ? "disabled" : ""}>${ICON("upload")}Create &amp; upload to drone</button></div>`;
    $("#b-upload").addEventListener("click", upload);
  }
  async function upload(e) {
    const b = e.currentTarget; b.disabled = true;
    b.innerHTML = `${ICON("upload")}Uploading and verifying…`;
    try {
      const m = await api("/api/missions", { method: "POST", body: previewPlan.req });
      try {
        await api(`/api/missions/${encodeURIComponent(m.id)}/upload`, { method: "POST", timeout: 60000 });
      } catch (err) {
        toast(`Mission ${m.id} saved, but the upload failed: ${err.message}`, "error", 9000);
        b.disabled = false; b.innerHTML = `${ICON("upload")}Retry upload`;
        b.onclick = async () => { b.disabled = true; try { await api(`/api/missions/${encodeURIComponent(m.id)}/upload`, { method: "POST", timeout: 60000 }); current = await api(`/api/missions/${encodeURIComponent(m.id)}`); showPreflight(); } catch (e2) { toast(e2.message, "error"); b.disabled = false; } };
        return;
      }
      toast("Mission uploaded and verified by reading it back from the flight controller.", "ok");
      current = await api(`/api/missions/${encodeURIComponent(m.id)}`);
      showPreflight();
    } catch (err) { toast(err.message, "error", 9000); b.disabled = false; b.innerHTML = `${ICON("upload")}Create &amp; upload to drone`; }
  }

  function stopPreflight() { clearInterval(preflightTimer); preflightTimer = null; preflightState = null; }
  function showPreflight() {
    stopPreflight();
    panel.innerHTML = `
      <p class="small"><span class="tag tag--ok">${ICON("check")}Uploaded &amp; verified</span> <span class="mono">${esc(current.id)}</span> · ${esc(current.field?.name || "")}</p>
      <dl class="kv small" style="margin-top:10px"><dt>Route</dt><dd>${fmt(current.plan.total_length_m, 0)} m · ${current.plan.lines} lines</dd>
        <dt>Altitude / speed</dt><dd>${current.plan.params.altitude_m} m · ${current.plan.params.speed_ms} m/s</dd>
        <dt>Estimate</dt><dd>${dur(current.plan.est_duration_s)} · ${fmt(current.plan.est_battery_pct, 0)}% battery</dd></dl>
      <h3 style="margin:16px 0 6px">Pre-flight checks</h3>
      <ul class="checks" id="checks"><li><span></span><span class="muted">Running checks…</span></li></ul>
      <div class="row" style="margin-top:14px"><button class="btn" id="b-start" disabled>${ICON("play")}Start mission</button>
        <button class="btn btn--ghost" id="b-back">Plan another</button></div>
      <p class="small muted" id="start-why" style="margin-top:8px"></p>`;
    drawRoute(current.plan.waypoints);
    $("#b-back").addEventListener("click", () => { routeLayer.clearLayers(); planForm(); });
    $("#b-start").addEventListener("click", start);
    const run = async () => {
      try { preflightState = await api(`/api/missions/${encodeURIComponent(current.id)}/preflight`); }
      catch (e) { $("#start-why").textContent = e.message; return; }
      const pf = preflightState;
      $("#checks").innerHTML = pf.checks.map((c) => `<li data-status="${c.status}">${ICON(c.status === "pass" ? "check" : c.status === "warn" ? "alert" : "x")}
        <div>${esc(c.label)}${c.critical ? "" : ' <span class="muted">(advisory)</span>'}<small>${esc(c.detail)}</small></div></li>`).join("");
      const ready = pf.ready && pf.token;
      $("#b-start").disabled = !ready;
      $("#start-why").textContent = ready
        ? "All critical checks pass. Starting asks for a final confirmation."
        : pf.blocking.includes("active_mission") ? "Another mission is in progress."
          : `Launch blocked by ${pf.blocking.length} failing check${pf.blocking.length === 1 ? "" : "s"}. Checks refresh every 5 s.`;
    };
    run();
    preflightTimer = setInterval(run, 5000);
  }
  async function start() {
    const pf = preflightState;
    if (!pf?.token) return;
    const warns = pf.checks.filter((c) => c.status === "warn");
    const ok = await confirmDialog({
      title: "Start the inspection flight?",
      body: `<p>The drone will <strong>arm, take off to ${current.plan.params.altitude_m} m</strong>, fly ${current.plan.lines} survey lines over
        <strong>${esc(current.field?.name || "the field")}</strong> (about ${dur(current.plan.est_duration_s)}), then return and land on its own.</p>
        ${pf.sim ? `<p class="tag tag--info" style="margin-top:8px">Simulator: this flies the SITL drone, not the real one.</p>`
          : `<p class="tag tag--warn" style="margin-top:8px">REAL DRONE: the motors will arm and the aircraft will take off.</p>`}
        ${warns.length ? `<p style="margin-top:10px">Advisory warnings:</p><ul>${warns.map((w) => `<li>${esc(w.label)}: ${esc(w.detail)}</li>`).join("")}</ul>` : ""}`,
      confirmText: "Arm and start", kind: "warn",
      requireCheck: "The area is clear of people and a pilot with the transmitter is ready to take over.",
    });
    if (!ok) return;
    stopPreflight();
    const b = $("#b-start");
    if (b) { b.disabled = true; b.innerHTML = `${ICON("play")}Starting — waiting for the autopilot…`; }
    try {
      await api(`/api/missions/${encodeURIComponent(current.id)}/start`, { method: "POST", body: { confirm: true, token: pf.token }, timeout: 60000 });
      toast("Mission started: mode, arming and mission start were each acknowledged by the flight controller.", "ok", 8000);
    } catch (err) {
      toast(`Start stopped: ${err.message}`, "error", 12000);
      showPreflight();
    }
  }

  function activePanel(m, s) {
    const t = s.telemetry || {};
    if (lastActiveId !== m.id) {
      lastActiveId = m.id;
      panel.innerHTML = `<div class="stage"><strong id="a-stage"></strong><span class="mono small muted" id="a-id"></span></div>
        <div class="progress" style="margin-top:12px" role="progressbar" aria-label="Survey progress" aria-valuemin="0" aria-valuemax="100" id="a-prog"><i></i></div>
        <dl class="kv small" style="margin-top:12px" id="a-kv"></dl><p class="small" id="a-warn" style="margin-top:10px"></p>`;
    }
    $("#a-stage").textContent = m.stage_text;
    $("#a-id").textContent = m.id;
    $("#a-prog").setAttribute("aria-valuenow", String(Math.round(m.progress_pct)));
    $("#a-prog i").style.width = `${m.progress_pct}%`;
    $("#a-kv").innerHTML = `<dt>Survey progress</dt><dd>${fmt(m.progress_pct, 0)}% · waypoint ${m.seq != null ? Math.max(0, m.seq - m.first_wp_seq + 1) : "—"} of ${m.last_wp_seq - m.first_wp_seq + 1}</dd>
      <dt>Elapsed</dt><dd>${dur(m.elapsed_s)}</dd><dt>Time to finish</dt><dd>${m.eta_s != null ? `≈ ${dur(m.eta_s)}` : "—"}</dd>
      <dt>Battery</dt><dd>${t.battery_v != null ? `${fmt(t.battery_v, 2)} V` : "—"}${t.battery_pct != null ? ` · ${t.battery_pct}%` : ""}</dd>
      <dt>Distance flown</dt><dd>${m.distance_m} m</dd><dt>Captures</dt><dd>${m.captures}${m.skipped_captures ? ` (${m.skipped_captures} skipped: no camera frame)` : ""}</dd>`;
    $("#a-warn").innerHTML = m.link_lost ? `<span class="tag tag--bad">${ICON("alert")}Telemetry lost</span> The autopilot continues on its own and will fail-safe if needed.`
      : m.status === "pilot" ? `<span class="tag tag--warn">${ICON("alert")}Pilot in control</span> The flight mode was changed outside the console.` : "";
  }

  // ---------------------------------------------------------------- logs + live detections
  async function refreshLog() {
    const mid = live.state?.mission?.active?.id || current?.id;
    try {
      const ev = await api(`/api/mission/events${mid ? `?mission=${encodeURIComponent(mid)}` : ""}`);
      $("#cmd-log").innerHTML = ev.length ? ev.slice(0, 40).map((e) => `<li data-level="${e.level}"><time>${clock(e.t)}</time>
        <span>${e.kind === "ack" && e.data ? `<span class="ack">${ICON(e.data.ok ? "check" : "x")}</span> ` : ""}${esc(e.text)}</span></li>`).join("")
        : `<li><span></span><span class="muted">No commands sent yet.</span></li>`;
    } catch { /* keep the previous list */ }
  }
  const seen = new Set();
  function addLiveDet(d) {
    if (!d || seen.has(d.id)) return;
    seen.add(d.id);
    const el = $("#live-dets");
    if (el.querySelector(".empty")) el.innerHTML = "";
    const li = document.createElement("li");
    li.innerHTML = `${d.thumb ? `<img src="/captures/${encodeURI(d.thumb)}" alt="">` : "<span></span>"}<a href="#/log?id=${encodeURIComponent(d.id)}">
      <div>${kindTag(d.label, d.status)} <span class="mono small muted">${pct(d.p)}</span></div>
      <div class="small muted">${clock(d.t)} · ${d.lat != null ? `${d.lat.toFixed(5)}, ${d.lon.toFixed(5)}` : "no GPS position"}</div></a>`;
    el.prepend(li);
    while (el.children.length > 12) el.lastChild.remove();
    if (d.lat != null && kindOf(d.label, d.status) !== "none") detectionMarker(d).addTo(detLayer);
  }
  $("#live-dets").innerHTML = `<li style="display:block"><div class="empty" style="padding:12px">${ICON("leaf")}<span>Detections appear here during a flight or after a snapshot.</span></div></li>`;

  // ---------------------------------------------------------------- live tick
  const updateConn = connectionControl($("#conn-ctl"));
  async function onState(s) {
    updateConn(s);
    renderCamera(s);
    renderTelemetry(s);
    const t = s.telemetry || {};
    $("#head-tags").innerHTML = `${s.sim ? `<span class="tag tag--info">Simulator</span>` : `<span class="tag tag--warn">Real drone</span>`}
      <span class="tag ${t.link ? "tag--ok" : "tag--bad"}">${t.link ? "Drone linked" : "No drone link"}</span>
      ${t.link ? `<span class="tag ${t.armed ? "tag--warn" : ""}">${t.armed ? "Armed" : "Disarmed"}</span>` : ""}`;
    if (t.lat != null && map) {
      const ll = [t.lat, t.lon];
      if (!droneMarker) droneMarker = L.marker(ll, { icon: droneIcon(t.heading), zIndexOffset: 1000, title: "Drone" }).addTo(map);
      else { droneMarker.setLatLng(ll); droneMarker.setIcon(droneIcon(t.heading)); }
      if (t.armed) {
        const pts = trackLine.getLatLngs(), lastPt = pts.at(-1);
        if (!lastPt || lastPt.distanceTo(ll) > 0.8) trackLine.addLatLng(ll);
      }
      if (!centred || ($("#follow").checked && t.armed)) { if (!centred) map.setView(ll, 18); else map.panTo(ll, { animate: false }); centred = true; }
    }
    if (t.home && map) {
      const hl = [t.home.lat, t.home.lon];
      if (!homeMarker) homeMarker = L.marker(hl, { icon: homeIcon(), title: "Home (launch point)" }).addTo(map);
      else homeMarker.setLatLng(hl);
    }
    const m = s.mission?.active;
    $("#mc-tag").innerHTML = m ? `<span class="tag tag--accent">${esc(m.stage_text)}</span>` : "";
    if (m) {
      stopPreflight();
      if (activeId !== m.id) {
        activeId = m.id;
        const md = await api(`/api/missions/${encodeURIComponent(m.id)}`).catch(() => null);
        if (md) {
          drawRoute(md.plan.waypoints);
          if (loadedTrackFor !== m.id) { trackLine.setLatLngs(md.track.filter((p) => p.lat != null).map((p) => [p.lat, p.lon])); loadedTrackFor = m.id; }
          md.detections.forEach(addLiveDet);
          drawFields(md.field_id);
        }
      }
      activePanel(m, s);
      if (m.last_detection) addLiveDet(m.last_detection);
    } else if (activeId) {
      // The mission just ended: show its summary, then return to planning.
      const ended = activeId;
      activeId = null; lastActiveId = null;
      panel.innerHTML = `<p><span class="tag tag--accent">Mission ended</span></p><p class="small" style="margin-top:8px">The flight is over. See the result in <a href="#/missions/${encodeURIComponent(ended)}">mission ${esc(ended)}</a>.</p>
        <div class="row" style="margin-top:12px"><button class="btn btn--ghost" id="b-new">Plan another</button></div>`;
      $("#b-new").addEventListener("click", () => { trackLine.setLatLngs([]); routeLayer.clearLayers(); planForm(); });
    }
  }

  await loadFields();
  drawFields();
  if (!fields.length && map) map.setView(DEFAULT_CENTER, 17);
  if (!live.state?.mission?.active) planForm();
  const unsub = live.subscribe((s) => { onState(s).catch(console.error); });
  refreshLog();
  const logTimer = setInterval(refreshLog, 3000);
  return () => { unsub(); stopPreflight(); clearInterval(logTimer); $("#feed-img").src = ""; map?.remove(); };
}

