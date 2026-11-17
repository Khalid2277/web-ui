// Overview: the farm at a glance. Health gauge + what to do next | live aerial map | latest inspection +
// problem mix; then drone status, 24-hour activity, 30-day activity, alerts, recent detections, sensors.
import { api, live } from "../api.js";
import { burstChart, gauge, ringChart, waveChart } from "../charts.js";
import { detectionMarker, droneIcon, fieldStyle, fitTo, homeIcon, makeMap } from "../mapkit.js";
import { ICON, ago, area, date, dateTime, esc, fmt, infoTip, int, kindMark, kindOf, kindTag, labelName, metaRow, pct, seriesColor } from "../util.js";

const L = window.L;

export async function mount(root) {
  root.innerHTML = `
    <div class="page-head">
      <div><h1 id="ov-title">Farm overview</h1>${metaRow('<span id="ov-sub"></span>')}</div>
      <div class="row" id="ov-actions"><a class="btn btn--ghost" href="#/map">${ICON("map")}Open farm map</a><a class="btn" href="#/live">${ICON("route")}Plan inspection</a></div>
    </div>
    <div class="stage-grid">
      <div class="stack">
        <section class="card" aria-labelledby="hl-h"><div class="card__head"><h2 id="hl-h">${ICON("leaf")}<span>Health status<span class="card__sub" id="hl-basis">Latest inspection of each field</span></span></h2>
          ${infoTip("Healthy leaves ÷ (healthy + diseased/deficient/pest) leaves the model classified confidently. Uncertain leaves are not counted. Needs at least 20 leaves.")}</div>
          <div id="health" style="display:grid;justify-items:center"></div></section>
        <section class="card" aria-labelledby="todo-h"><div class="card__head"><h2 id="todo-h">${ICON("shield")}<span>What to do next<span class="card__sub">From the last 30 days of findings</span></span></h2></div>
          <ul class="alerts" id="todo"></ul></section>
      </div>
      <section class="stage-center" aria-label="Farm map">
        <div class="map" id="ov-map"></div>
        <div class="stage-float tl"><span class="pill" id="pill-fields">${ICON("map")}Fields</span><span class="pill" id="pill-drone">${ICON("drone")}Drone</span></div>
        <div class="stage-float bl" id="field-chips"></div>
      </section>
      <div class="stack">
        <section class="card" aria-labelledby="li-h"><div class="card__head"><h2 id="li-h">${ICON("history")}<span>Latest inspection<span class="card__sub" id="li-when">—</span></span></h2></div>
          <div id="last"></div></section>
        <section class="card" aria-labelledby="mix-h"><div class="card__head"><h2 id="mix-h">${ICON("chart")}<span>Problems found<span class="card__sub">Last 30 days, confident results</span></span></h2></div>
          <div id="mix"></div></section>
      </div>
    </div>
    <div class="kpis" id="kpis" style="margin-top:14px"></div>
    <div class="grid cols-12" style="margin-top:14px">
      <section class="card span-4" aria-labelledby="ls-h"><div class="card__head"><h2 id="ls-h">${ICON("drone")}<span>Drone &amp; system<span class="card__sub" id="ls-age"></span></span></h2></div>
        <ul class="status-list" id="live-status" style="grid-template-columns:1fr 1fr"></ul></section>
      <section class="card span-4" aria-labelledby="hr-h"><div class="card__head"><h2 id="hr-h">${ICON("clock")}<span>Last 24 hours<span class="card__sub">Captures analysed per hour</span></span></h2></div>
        <div id="burst"></div></section>
      <section class="card span-4" aria-labelledby="wv-h"><div class="card__head"><h2 id="wv-h">${ICON("chart")}<span>Problems per day<span class="card__sub">Last 30 days</span></span></h2></div>
        <div id="wave"></div><p class="small muted" id="wave-note" style="margin-top:8px"></p></section>
      <section class="card span-6" aria-labelledby="al-h"><div class="card__head"><h2 id="al-h">${ICON("alert")}<span>Alerts<span class="card__sub">Last 24 hours</span></span></h2></div>
        <ul class="alerts" id="alerts"></ul></section>
      <section class="card span-6" aria-labelledby="rd-h"><div class="card__head"><h2 id="rd-h">${ICON("leaf")}<span>Recent detections</span></h2><a class="small" href="#/log">Disease log</a></div>
        <ul class="det-list" id="recent"></ul></section>
      <section class="card span-12" aria-labelledby="fs-h"><div class="card__head"><h2 id="fs-h">${ICON("thermo")}<span>Field sensors<span class="card__sub">BME680 + soil moisture nodes</span></span></h2></div>
        <div id="sensors"></div></section>
    </div>`;
  const $ = (s) => root.querySelector(s);

  // ---------------------------------------------------------------- map stage
  const map = makeMap($("#ov-map"), { zoom: 17, layersControl: false });
  map?.zoomControl.setPosition("bottomright");
  const fieldL = L.featureGroup().addTo(map), detL = L.layerGroup().addTo(map);
  let drone = null, home = null, fields = [], fitted = false;

  async function loadMap() {
    let data;
    try { [data, fields] = await Promise.all([api(`/api/map?from=${Date.now() / 1000 - 30 * 86400}`), api("/api/fields")]); } catch { return; }
    if (!$("#ov-map")) return;
    fieldL.clearLayers(); detL.clearLayers();
    const names = Object.fromEntries(fields.map((f) => [f.id, f.name]));
    for (const f of fields) {
      const h = f.summary.health.pct;
      L.polygon(f.polygon, { ...fieldStyle(false), color: h == null ? "#ffffff" : h >= 85 ? "#5fd39a" : h >= 65 ? "#f6c454" : "#ff8f7a", dashArray: null, weight: 2.5, fillOpacity: 0.12 })
        .bindTooltip(`${f.name} · ${h == null ? "no inspection" : `${fmt(h, 0)}% healthy`}`, { sticky: true }).addTo(fieldL);
    }
    data.detections.filter((d) => ["disease", "deficiency", "pest"].includes(d.kind) && d.status === "confident")
      .forEach((d) => detectionMarker(d, names[d.field_id]).addTo(detL));
    $("#pill-fields").innerHTML = `${ICON("map")}<b>${fields.length}</b> field${fields.length === 1 ? "" : "s"} · ${area(fields.reduce((a, f) => a + f.area_m2, 0))}`;
    $("#field-chips").innerHTML = fields.slice(0, 6).map((f) => `<button class="pill" data-id="${esc(f.id)}" style="cursor:pointer;border:1px solid var(--edge)">
      <i class="dot" data-ok="${f.summary.health.pct == null ? "warn" : f.summary.health.pct >= 85}"></i>${esc(f.name)}</button>`).join("")
      + (fields.length ? "" : `<a class="pill" href="#/map?draw=1">${ICON("plus")}Draw your first field</a>`);
    if (!fitted) fitted = fitTo(map, fields.flatMap((f) => f.polygon), 18);
  }
  $("#field-chips").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-id]");
    const f = b && fields.find((x) => x.id === b.dataset.id);
    if (f) fitTo(map, f.polygon, 19);
  });

  // ---------------------------------------------------------------- data cards
  async function load() {
    let ov, an;
    try { [ov, an] = await Promise.all([api("/api/overview"), api("/api/analytics?days=30")]); } catch (e) { if (root.contains($("#ov-sub"))) $("#ov-sub").textContent = e.message; return; }
    if (!$("#ov-title")) return;   // the page was left while loading
    $("#ov-title").textContent = ov.farm_name;
    $("#ov-sub").innerHTML = `${ICON("leaf")}${ov.fields ? `${area(ov.area_m2)} under watch` : "No fields mapped yet"}`;
    const h = ov.health;
    $("#hl-basis").textContent = h.basis || "";
    $("#health").innerHTML = `${gauge(h.pct, { label: "Crop health", size: 210 })}
      <div class="legend-dots" style="justify-content:center;margin-top:4px">
        <span><i style="background:var(--k-healthy)"></i>Healthy ${int(h.healthy)}</span><span><i style="background:var(--k-disease)"></i>Problem ${int(h.problem)}</span>
        <span><i style="background:var(--k-uncertain)"></i>Uncertain ${int(h.uncertain)}</span></div>
      ${h.reason ? `<p class="small muted" style="margin-top:10px;text-align:center">${esc(h.reason)}</p>` : ""}`;
    $("#todo").innerHTML = ov.top_problems.length ? ov.top_problems.map((p) => `<li data-level="warn">${kindMark(kindOf(p.label))}
        <div><a href="#/log?label=${encodeURIComponent(p.label)}" style="color:inherit;text-decoration:none"><strong>${esc(labelName(p.label))}</strong></a>
        <small>${esc(p.guidance || "Check the affected plants and confirm with an agronomist.")}</small></div><time>${p.n}×</time></li>`).join("")
        + `<li style="display:block;background:none;border:0;padding:2px 4px" class="small muted">General guidance — confirm with an agronomist before treating.</li>`
      : `<li style="display:block"><div class="empty" style="padding:14px">${ICON("check")}<span>No confident problems in the last 30 days.</span></div></li>`;

    const last = ov.last_inspection;
    if (!last) {
      $("#li-when").textContent = "No inspection flown yet";
      $("#last").innerHTML = `<div class="empty" style="padding:14px">${ICON("drone", "icon icon--lg")}<span>Plan an inspection flight on the Operations page.</span></div>`;
    } else {
      const f = fields.find((x) => x.id === last.field_id), st = last.stats || {};
      $("#li-when").textContent = `${dateTime(last.t)} · ${ago(last.t)}`;
      $("#last").innerHTML = `<dl class="kv" style="grid-template-columns:1fr auto">
          <dt>Field</dt><dd>${esc(f?.name || "—")}</dd>
          <dt>Result</dt><dd>${last.status === "completed" ? '<span class="tag tag--ok">Completed</span>' : '<span class="tag tag--warn">Ended early</span>'}</dd>
          <dt>Survey flown</dt><dd>${st.survey_progress != null ? `${st.survey_progress}%` : "—"}</dd>
          <dt>Captures</dt><dd>${int(st.captures)}</dd><dt>Flight time</dt><dd>${st.duration_s != null ? `${Math.round(st.duration_s / 60)} min` : "—"}</dd>
          <dt>Field health</dt><dd>${f?.summary.health.pct != null ? `${fmt(f.summary.health.pct, 0)}%` : "—"}</dd></dl>
        <a class="btn btn--ghost btn--sm" style="margin-top:12px;width:100%" href="#/missions/${encodeURIComponent(last.mission_id)}">Open mission report</a>`;
    }
    const probs = an.distribution.by_label.filter((r) => ["disease", "deficiency", "pest"].includes(r.kind));
    if (!probs.length) $("#mix").innerHTML = `<div class="empty" style="padding:14px">${ICON("check")}<span>No problems found in the last 30 days.</span></div>`;
    else {
      const top = probs.slice(0, 4), rest = probs.slice(4).reduce((a, r) => a + r.n, 0);
      ringChart($("#mix"), { items: [...top.map((r) => ({ name: labelName(r.label), value: r.n, color: seriesColor(r.label) })),
        ...(rest ? [{ name: "Other", value: rest, color: "var(--s-other)" }] : [])],
        center: String(probs.reduce((a, r) => a + r.n, 0)), sub: "detections", caption: "Problems found in the last 30 days", size: 180 });
    }

    $("#kpis").innerHTML = [
      ["Farm area", ov.area_m2 != null ? area(ov.area_m2).replace(/ (m²|ha)$/, "<small>$1</small>") : null, `${ov.fields} field${ov.fields === 1 ? "" : "s"}`, "No fields drawn"],
      ["Problems found", int(ov.detections.problems), `${int(ov.detections.problems_week)} in the last 7 days`],
      ["Captures analysed", int(ov.detections.total), `${int(ov.detections.week)} in the last 7 days`],
      ["Inspection flights", int(ov.missions), last ? `last ${ago(last.t)}` : "none yet"],
    ].map(([label, v, sub, na]) => `<div class="kpi"><div class="kpi__label">${esc(label)}</div>
        ${v != null ? `<div class="kpi__value">${v}</div><div class="kpi__sub">${esc(sub || "")}</div>` : `<span class="kpi__na">${esc(na)}</span>`}</div>`).join("");

    const hr = ov.hourly, hl = hr.captures.map((_, i) => new Date((hr.end - (24 - i) * 3600) * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }));
    const capTotal = hr.captures.reduce((a, b) => a + b, 0);
    burstChart($("#burst"), { values: hr.captures, labels: hl, center: String(capTotal), sub: "CAPTURES · 24 H", caption: "Captures analysed per hour, last 24 hours", size: 230 });
    const ser = an.diseases, perDay = Array.from({ length: ser.days }, (_, i) => Object.values(ser.series).reduce((a, v) => a + v[i], 0));
    waveChart($("#wave"), { values: perDay, labels: perDay.map((_, i) => date(ser.start + i * 86400)), caption: "Confident problems detected per day, last 30 days" });
    $("#wave-note").textContent = ser.total ? `${ser.total} confident problem detections in 30 days.` : "No problems detected in the last 30 days.";

    $("#alerts").innerHTML = ov.alerts.length ? ov.alerts.map((a) => `<li data-level="${a.level}">${ICON(a.level === "error" ? "x" : "alert")}
        <div><a href="${a.kind === "detection" ? `#/log?id=${encodeURIComponent(a.id)}` : a.kind === "mission" ? `#/missions/${encodeURIComponent(a.id)}` : "#/system"}" style="color:inherit;text-decoration:none">${esc(a.title)}</a><small>${esc(a.detail)}</small></div>
        <time>${esc(ago(a.t))}</time></li>`).join("")
      : `<li style="display:block"><div class="empty" style="padding:14px">${ICON("check")}<span>No alerts in the last 24 hours.</span></div></li>`;
    $("#recent").innerHTML = ov.recent.length ? ov.recent.slice(0, 5).map((d) => `<li>
        ${d.thumb ? `<img src="/captures/${encodeURI(d.thumb)}" alt="" loading="lazy">` : "<span></span>"}
        <a href="#/log?id=${encodeURIComponent(d.id)}"><div>${kindTag(d.label, d.status)} <span class="small muted">${pct(d.p)}</span></div>
        <div class="small muted">${esc(ago(d.t))} · ${d.source === "mission" ? "inspection flight" : "snapshot"}${d.lat == null ? " · no GPS" : ""}</div></a></li>`).join("")
      : `<li style="display:block"><div class="empty">${ICON("leaf", "icon icon--lg")}<strong>No detections yet</strong><span>Fly an inspection or take a snapshot on Operations.</span></div></li>`;
  }

  // ---------------------------------------------------------------- live
  let sensorsKey = "";
  function renderLive(s) {
    const t = s.telemetry || {}, m = s.mission?.active, sys = s.system || {}, cam = s.camera || {};
    const ok = (v) => (v === true ? "true" : v === false ? "false" : "warn");
    const fix = t.gps_fix ?? 0;
    const items = [
      ["drone", "Drone", s.telemetry_enabled ? (t.link ? `${t.armed ? "Armed" : "Ready"} · ${esc(t.mode || "")}` : "No link") : "Disabled", t.link ? (t.armed ? "warn" : true) : false],
      ["battery", "Battery", t.battery_v != null ? `${fmt(t.battery_v, 1)} V${t.battery_pct != null ? ` · ${t.battery_pct}%` : ""}` : "—", t.battery_v != null ? true : null],
      ["satellite", "GPS", t.link ? `${["None", "No fix", "2D", "3D", "DGPS", "RTK", "RTK"][fix] || fix} · ${t.gps_sats ?? 0} sats` : "—", t.link ? fix >= 3 : null],
      ["route", "Mission", m ? `${esc(m.stage_text)} ${fmt(m.progress_pct, 0)}%` : "Idle", m ? "warn" : true],
      ["camera", "Camera", cam.status === "live" ? "Live" : "Offline", cam.status === "live"],
      ["cpu", "Pi", sys.cpu_temp != null ? `${fmt(sys.cpu_temp, 0)} °C` : sys.t ? "OK" : "—", sys.undervoltage_now ? false : sys.t ? true : null],
    ];
    $("#live-status").innerHTML = items.map(([icon, label, v, state]) => `<li data-ok="${ok(state)}">${ICON(icon)}<div><b>${label}</b><span class="v"><span class="dot"></span>${v}</span></div></li>`).join("");
    $("#ls-age").textContent = t.heartbeat_age_s != null ? `Telemetry ${fmt(t.heartbeat_age_s, 1)} s ago` : "No telemetry";
    $("#pill-drone").innerHTML = `${ICON("drone")}${t.link ? (m ? esc(m.stage_text) : t.armed ? "Armed" : "On the ground") : "Drone offline"}`;
    if (map && t.lat != null && t.link) {
      if (!drone) drone = L.marker([t.lat, t.lon], { icon: droneIcon(t.heading), zIndexOffset: 1000, title: "Drone" }).addTo(map);
      else { drone.setLatLng([t.lat, t.lon]); drone.setIcon(droneIcon(t.heading)); }
      if (!fitted) { map.setView([t.lat, t.lon], 18); fitted = true; }
    }
    if (map && t.home && !home) home = L.marker([t.home.lat, t.home.lon], { icon: homeIcon(), title: "Home" }).addTo(map);
    const env = s.env, key = `${env?.readings}-${env?.last_t}`;
    if (key !== sensorsKey) { sensorsKey = key; renderSensors(env); }
  }
  function renderSensors(env) {
    const el = $("#sensors");
    if (!env || !env.readings) {
      el.innerHTML = `<div class="empty" style="padding:12px">${ICON("thermo", "icon icon--lg")}<strong>No field sensors connected</strong>
        <span>When BME680 / soil-moisture nodes post to <code>/api/sensors</code>, temperature, humidity, soil moisture and disease-risk rules appear here.</span></div>`;
      return;
    }
    const l = env.latest || {}, risks = Object.entries(env.diseases || {});
    el.innerHTML = `<div class="tiles"><div class="tile"><b>Temperature</b><span>${fmt(l.temp_c, 1)} °C</span></div>
        <div class="tile"><b>Humidity</b><span>${fmt(l.rh, 0)} %</span></div><div class="tile"><b>Soil moisture</b><span>${fmt(l.soil_moisture_pct, 0)} %</span></div>
        <div class="tile"><b>History</b><span>${fmt(env.hours_of_data, 0)} h</span></div></div>
      ${risks.length ? `<ul class="alerts" style="margin-top:10px">${risks.map(([k, r]) => `<li data-level="${r.risk > 0.6 ? "error" : "warn"}">${ICON("alert")}<div>${esc(labelName(k))} risk ${pct(r.risk)}<small>${esc(r.why || "")}</small></div><span></span></li>`).join("")}</ul>` : ""}
      <p class="small muted" style="margin-top:8px">Last reading ${esc(ago(env.last_t))}.</p>`;
  }

  await loadMap();
  await load();
  const unsub = live.subscribe(renderLive);
  const t1 = setInterval(load, 30000), t2 = setInterval(loadMap, 60000);
  return () => { unsub(); clearInterval(t1); clearInterval(t2); map?.remove(); };
}
