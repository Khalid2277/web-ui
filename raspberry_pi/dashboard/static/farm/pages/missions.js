// Mission history: list of all missions, and a detail view (route vs flown track, coverage, events, findings).
import { api, live } from "../api.js";
import { detectionMarker, fieldStyle, fitTo, homeIcon, makeMap } from "../mapkit.js";
import { metaRow, ICON, clock, confirmDialog, dateTime, dur, esc, fmt, int, kindTag, pct, toast } from "../util.js";

const L = window.L;
const STATUS = { planned: ["Planned", ""], uploaded: ["On the drone", "tag--info"], completed: ["Completed", "tag--ok"], aborted: ["Ended early", "tag--warn"],
  failed: ["Failed to start", "tag--bad"], starting: ["Starting", "tag--accent"], takeoff: ["Taking off", "tag--accent"], surveying: ["Surveying", "tag--accent"],
  paused: ["Paused", "tag--warn"], returning: ["Returning", "tag--accent"], landing: ["Landing", "tag--accent"], pilot: ["Pilot control", "tag--warn"] };
const statusTag = (s) => `<span class="tag ${(STATUS[s] || ["", ""])[1]}">${esc((STATUS[s] || [s])[0])}</span>`;

export async function mount(root, { arg }) {
  return arg ? detail(root, arg) : list(root);
}

async function list(root) {
  root.innerHTML = `<div class="page-head"><div><h1>Inspection missions</h1>${metaRow()}
      <p>Every planned and flown mission. Flown missions are kept permanently with their track, events and findings.</p></div>
      <a class="btn" href="#/live">${ICON("route")}Plan a mission</a></div>
    <section class="card"><div class="table-wrap"><table><thead><tr><th>Mission</th><th>Field</th><th>Status</th><th>Date</th>
      <th class="hide-sm">Duration</th><th class="hide-sm">Captures</th><th>Problems</th></tr></thead><tbody id="rows"></tbody></table></div></section>`;
  let ms;
  try { ms = await api("/api/missions"); } catch (e) { root.querySelector("#rows").innerHTML = `<tr><td colspan="7">${esc(e.message)}</td></tr>`; return; }
  root.querySelector("#rows").innerHTML = ms.length ? ms.map((m) => `<tr data-id="${esc(m.id)}" tabindex="0">
      <td class="mono small">${esc(m.id)}${m.sim ? ' <span class="tag tag--info">sim</span>' : ""}</td><td>${esc(m.field_name || "—")}</td><td>${statusTag(m.status)}</td>
      <td class="small mono">${esc(dateTime(m.started || m.created))}</td><td class="hide-sm mono">${m.stats ? dur(m.stats.duration_s) : "—"}</td>
      <td class="hide-sm mono">${int(m.captures)}</td><td class="mono">${int(m.problems)}</td></tr>`).join("")
    : `<tr><td colspan="7"><div class="empty">${ICON("drone", "icon icon--lg")}<strong>No missions yet</strong><span>Plan one on the Operations page.</span></div></td></tr>`;
  const go = (e) => { const tr = e.target.closest("tr[data-id]"); if (tr && (e.type === "click" || e.key === "Enter")) location.hash = `#/missions/${encodeURIComponent(tr.dataset.id)}`; };
  root.querySelector("#rows").addEventListener("click", go);
  root.querySelector("#rows").addEventListener("keydown", go);
}

async function detail(root, id) {
  let m;
  try { m = await api(`/api/missions/${encodeURIComponent(id)}`); } catch (e) {
    root.innerHTML = `<div class="empty">${ICON("alert", "icon icon--lg")}<strong>${esc(e.message)}</strong><a href="#/missions">Back to missions</a></div>`; return;
  }
  const p = m.plan, st = m.stats || {}, h = m.health;
  const flown = ["completed", "aborted"].includes(m.status);
  root.innerHTML = `
    <div class="page-head"><div><a href="#/missions" class="small">← All missions</a><h1 style="margin-top:6px">${esc(m.field?.name || "Mission")} <span class="mono muted" style="font-size:.6em">${esc(m.id)}</span></h1>
      <div class="row" style="margin-top:8px">${statusTag(m.status)}${m.sim ? '<span class="tag tag--info">Simulator flight</span>' : ""}<span class="small muted">${esc(dateTime(m.started || m.created))}</span></div>
      ${m.end_reason ? `<p style="margin-top:8px">${esc(m.end_reason)}</p>` : ""}</div>
      <div class="row">${["planned", "uploaded"].includes(m.status) && !m.track.length ? `<button class="btn btn--ghost" id="del">${ICON("trash")}Delete</button>` : ""}
        ${m.detections.length ? `<a class="btn btn--ghost" href="/api/detections.csv?mission=${encodeURIComponent(m.id)}" download>${ICON("download")}Findings CSV</a>` : ""}</div></div>
    <div class="kpis">
      <div class="kpi"><div class="kpi__label">Duration</div><div class="kpi__value" style="font-size:1.3rem">${flown ? dur(st.duration_s) : "—"}</div><div class="kpi__sub">planned ≈ ${dur(p.est_duration_s)}</div></div>
      <div class="kpi"><div class="kpi__label">Survey completed</div><div class="kpi__value">${flown ? `${st.survey_progress ?? 0}<small>%</small>` : "—"}</div><div class="kpi__sub">${p.lines} lines planned</div></div>
      <div class="kpi"><div class="kpi__label">Field inspected</div>${m.coverage ? `<div class="kpi__value">${fmt(m.coverage.pct, 0)}<small>%</small></div><div class="kpi__sub">estimate, ${fmt(p.swath_m, 1)} m swath</div>` : `<span class="kpi__na">${flown ? "No track recorded" : "Not flown"}</span>`}</div>
      <div class="kpi"><div class="kpi__label">Crop health</div>${h.pct != null ? `<div class="kpi__value">${fmt(h.pct, 0)}<small>%</small></div><div class="kpi__sub">${h.healthy} healthy · ${h.problem} problem leaves</div>` : `<span class="kpi__na">${esc(flown ? h.reason : "Not flown")}</span>`}</div>
      <div class="kpi"><div class="kpi__label">Captures</div><div class="kpi__value">${int(m.detections.length)}</div><div class="kpi__sub">${st.skipped_captures ? `${st.skipped_captures} skipped (no camera frame)` : `every ≈ ${fmt(p.capture_spacing_m, 0)} m`}</div></div>
      <div class="kpi"><div class="kpi__label">Battery</div><div class="kpi__value" style="font-size:1.3rem">${st.battery_start_v != null ? `${fmt(st.battery_start_v, 2)} → ${fmt(st.battery_end_v, 2)} V` : "—"}</div><div class="kpi__sub">planned ≈ ${fmt(p.est_battery_pct, 0)}% of endurance</div></div>
    </div>
    <div class="grid cols-12" style="margin-top:16px">
      <section class="card span-8"><div class="card__head"><h2>${ICON("map")}Route and flown track</h2></div><div class="map" id="map"></div>
        <div class="map-legend"><span><i style="display:inline-block;width:18px;border-top:2px dashed #f4f1e6;outline:3px solid #2a3a2f;background:#2a3a2f;vertical-align:middle"></i> Planned route</span>
          <span><i style="display:inline-block;width:18px;border-top:3px solid var(--accent);vertical-align:middle"></i> Flown track</span>
          <span><i style="display:inline-block;width:14px;height:14px;background:rgba(200,241,105,.35);vertical-align:-2px"></i> Inspected cells</span></div></section>
      <section class="card span-4"><div class="card__head"><h2>${ICON("route")}Plan</h2></div>
        <dl class="kv small"><dt>Altitude</dt><dd>${p.params.altitude_m} m</dd><dt>Speed</dt><dd>${p.params.speed_ms} m/s</dd>
          <dt>Line spacing</dt><dd>${fmt(p.line_spacing_m, 1)} m (${Math.round(p.params.sidelap * 100)}% side-lap)</dd>
          <dt>Route length</dt><dd>${fmt(p.total_length_m, 0)} m</dd><dt>Flown</dt><dd>${st.distance_m != null ? `${st.distance_m} m` : "—"}</dd>
          <dt>Max altitude</dt><dd>${st.max_alt_m != null ? `${st.max_alt_m} m` : "—"}</dd><dt>Verified on drone</dt><dd>${p.verified ? "Yes" : "No"}</dd></dl>
        <details class="table-view"><summary>Assumptions</summary><ul class="small muted">${p.assumptions.map((a) => `<li>${esc(a)}</li>`).join("")}</ul></details></section>
      <section class="card span-6"><div class="card__head"><h2>${ICON("history")}Event log</h2><span class="small muted">${m.events.length} events</span></div>
        <ul class="cmd-log" style="max-height:420px">${m.events.slice().reverse().map((e) => `<li data-level="${e.level}"><time>${clock(e.t)}</time><span>${esc(e.text)}</span></li>`).join("") || "<li><span></span><span>No events.</span></li>"}</ul></section>
      <section class="card span-6"><div class="card__head"><h2>${ICON("leaf")}Findings</h2><a class="small" href="#/log?mission=${encodeURIComponent(m.id)}">Open in disease log</a></div>
        <ul class="det-list" style="max-height:420px;overflow-y:auto">${m.detections.filter((d) => d.kind !== "none").slice().reverse().slice(0, 60).map((d) => `<li>
          ${d.thumb ? `<img src="/captures/${encodeURI(d.thumb)}" alt="" loading="lazy">` : "<span></span>"}<a href="#/log?id=${encodeURIComponent(d.id)}">
          <div>${kindTag(d.label, d.status)} <span class="mono small muted">${pct(d.p)}</span></div><div class="small muted">${clock(d.t)} · ${d.n_leaves} leaves</div></a></li>`).join("")
          || `<li style="display:block"><div class="empty">${ICON("leaf")}<span>No captures in this mission.</span></div></li>`}</ul></section>
    </div>`;
  const map = makeMap(root.querySelector("#map"));
  if (map) {
    if (m.field) L.polygon(m.field.polygon, fieldStyle(true)).addTo(map);
    m.coverage_cells.forEach((c) => L.polygon(c, { stroke: false, fillColor: "#c8f169", fillOpacity: 0.3, interactive: false }).addTo(map));
    L.polyline(p.waypoints, { color: "#f4f1e6", weight: 2, dashArray: "6 6" }).addTo(map);
    const tr = m.track.filter((q) => q.lat != null).map((q) => [q.lat, q.lon]);
    if (tr.length) { L.polyline(tr, { color: "#c8f169", weight: 3 }).addTo(map); L.marker(tr[0], { icon: homeIcon(), title: "Launch" }).addTo(map); }
    m.detections.filter((d) => d.lat != null && d.kind !== "none").forEach((d) => detectionMarker(d, m.field?.name).addTo(map));
    fitTo(map, [...(m.field?.polygon || []), ...p.waypoints, ...tr]);
  }
  root.querySelector("#del")?.addEventListener("click", async () => {
    if (live.state?.local === false) { toast("Deleting needs the local network.", "warn"); return; }
    if (!(await confirmDialog({ title: "Delete this mission?", body: "<p>It was never flown, so nothing is lost except the plan.</p>", confirmText: "Delete", kind: "danger" }))) return;
    try { await api(`/api/missions/${encodeURIComponent(id)}`, { method: "DELETE" }); toast("Mission deleted.", "ok"); location.hash = "#/missions"; }
    catch (e) { toast(e.message, "error"); }
  });
  return () => map?.remove();
}
