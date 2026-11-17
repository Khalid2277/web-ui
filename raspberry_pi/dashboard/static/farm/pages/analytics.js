// Farm analytics: health trend, disease trend, distribution, fields, change between inspections,
// attention areas, condition x field heatmap, mission statistics. Unavailable figures say why.
import { api } from "../api.js";
import { barChart, gauge, hbarChart, lineChart, ringChart } from "../charts.js";
import { metaRow, ICON, date, dateTime, dur, esc, fmt, infoTip, int, labelName, seriesColor } from "../util.js";

const SLOTS = ["early_blight", "late_blight", "bacterial_spot", "septoria_leaf_spot", "leaf_mold", "yellow_leaf_curl_virus", "target_spot", "leaf_miner"];

export async function mount(root, { params }) {
  const days = +params.days || 30;
  root.innerHTML = `
    <div class="page-head"><div><h1>Farm analytics</h1>${metaRow()}
      <p>Computed only from stored captures and flights. Where there is not enough data, the figure is left empty with the reason.</p></div>
      <label class="field" style="min-width:180px">Period<select id="days">${[7, 30, 90, 365].map((d) => `<option value="${d}" ${d === days ? "selected" : ""}>Last ${d} days</option>`).join("")}</select></label></div>
    <div id="body"><div class="skeleton" style="height:300px"></div></div>`;
  root.querySelector("#days").addEventListener("change", (e) => { location.hash = `#/analytics?days=${e.target.value}`; });
  let a;
  try { a = await api(`/api/analytics?days=${days}`); } catch (e) {
    root.querySelector("#body").innerHTML = `<div class="empty">${ICON("alert", "icon icon--lg")}<strong>${esc(e.message)}</strong></div>`; return;
  }
  const fh = a.farm_health, ms = a.missions;
  const problemsInPeriod = a.distribution.by_label.filter((r) => ["disease", "deficiency", "pest"].includes(r.kind)).reduce((s, r) => s + r.n, 0);
  root.querySelector("#body").innerHTML = `
    <div class="kpis">
      <div class="kpi"><div class="kpi__label">Crop health ${infoTip(fh.basis || "")}</div>${fh.pct != null ? `<div class="kpi__value">${fmt(fh.pct, 0)}<small>%</small></div><div class="kpi__sub">${esc(fh.basis)}</div>` : `<span class="kpi__na">${esc(fh.reason)}</span>`}</div>
      <div class="kpi"><div class="kpi__label">Problems detected</div><div class="kpi__value">${int(problemsInPeriod)}</div><div class="kpi__sub">confident results, last ${days} days</div></div>
      <div class="kpi"><div class="kpi__label">Inspection flights</div><div class="kpi__value">${int(ms.flown)}</div><div class="kpi__sub">${int(ms.completed)} completed</div></div>
      <div class="kpi"><div class="kpi__label">Flight time</div><div class="kpi__value" style="font-size:1.35rem">${ms.flight_time_s ? dur(ms.flight_time_s) : "—"}</div><div class="kpi__sub">${ms.distance_m ? `${int(ms.distance_m)} m flown` : "no flights yet"}</div></div>
      <div class="kpi"><div class="kpi__label">Captures from flights</div><div class="kpi__value">${int(ms.captures)}</div><div class="kpi__sub">${ms.avg_duration_s ? `avg flight ${dur(ms.avg_duration_s)}` : ""}</div></div>
    </div>
    <div class="grid cols-12" style="margin-top:16px">
      <section class="card span-8"><div class="card__head"><h2>${ICON("chart")}Crop health over time ${infoTip(`Each point is one inspection flight (or one day of snapshots) with at least ${a.health.min_leaves} confidently classified leaves.`)}</h2></div><div id="c-health"></div></section>
      <section class="card span-4"><div class="card__head"><h2>${ICON("leaf")}Healthy vs problem leaves</h2></div><div id="c-leaves"></div></section>
      <section class="card span-7"><div class="card__head"><h2>${ICON("chart")}Problems detected per day</h2></div><div id="c-trend"></div></section>
      <section class="card span-5"><div class="card__head"><h2>${ICON("leaf")}Which problems</h2><span class="small muted">last ${days} days</span></div><div id="c-dist"></div></section>
      <section class="card span-6"><div class="card__head"><h2>${ICON("map")}Fields</h2></div><div id="c-fields"></div></section>
      <section class="card span-6"><div class="card__head"><h2>${ICON("history")}Change since the previous inspection</h2></div><div id="c-change"></div></section>
      <section class="card span-12"><div class="card__head"><h2>${ICON("grid")}Condition × field heatmap</h2><span class="small muted">all time, confident results</span></div><div id="c-heat"></div></section>
      <section class="card span-6"><div class="card__head"><h2>${ICON("alert")}Areas needing attention</h2></div><div id="c-att"></div></section>
      <section class="card span-6"><div class="card__head"><h2>${ICON("drone")}Inspection flights</h2></div><div id="c-missions"></div></section>
    </div>`;
  const $ = (s) => root.querySelector(s);
  const empty = (el, text, icon = "chart") => { $(el).innerHTML = `<div class="empty">${ICON(icon, "icon icon--lg")}<span>${esc(text)}</span></div>`; };

  // Health trend
  const insp = a.health.inspections.filter((p) => p.pct != null), man = a.health.manual_daily.filter((p) => p.pct != null);
  const draw = () => {
    if (!lineChart($("#c-health"), { series: [
      ...(insp.length ? [{ name: "Inspection flights", color: "var(--s3)", points: insp.map((p) => [p.t, p.pct]) }] : []),
      ...(man.length ? [{ name: "Manual snapshots (daily)", color: "var(--s1)", points: man.map((p) => [p.t, p.pct]) }] : []),
    ], yMax: 100, yLabel: "%", format: (v) => v.toFixed(0), caption: "Crop health percentage over time" })) {
      empty("#c-health", `Not enough data yet: a point needs at least ${a.health.min_leaves} confidently classified leaves.`);
    }
    const days0 = a.diseases.start, labels = Object.keys(a.diseases.series);
    if (!a.diseases.total) empty("#c-trend", `No problems detected in the last ${days} days.`, "check");
    else {
      const main = labels.filter((l) => SLOTS.includes(l)), rest = labels.filter((l) => !SLOTS.includes(l));
      const series = main.map((l) => ({ name: labelName(l), color: seriesColor(l), values: a.diseases.series[l] }));
      if (rest.length) series.push({ name: "Other", color: "var(--s-other)", values: a.diseases.series[rest[0]].map((_, i) => rest.reduce((s, l) => s + a.diseases.series[l][i], 0)) });
      barChart($("#c-trend"), { categories: Array.from({ length: a.diseases.days }, (_, i) => date(days0 + i * 86400)), series, caption: "Problems detected per day" });
    }
  };
  draw();

  const lv = a.distribution.leaves;
  if (lv.healthy + lv.problem + lv.uncertain === 0) empty("#c-leaves", "No leaves analysed in this period.", "leaf");
  else ringChart($("#c-leaves"), { items: [{ name: "Healthy", value: lv.healthy, color: "var(--k-healthy)" },
    { name: "Problem", value: lv.problem, color: "var(--k-disease)" }, { name: "Uncertain", value: lv.uncertain, color: "var(--k-uncertain)" }],
    center: lv.healthy + lv.problem ? `${Math.round((100 * lv.healthy) / (lv.healthy + lv.problem))}%` : "—", sub: "healthy", caption: "Healthy versus problem leaves" });

  const probs = a.distribution.by_label.filter((r) => ["disease", "deficiency", "pest"].includes(r.kind));
  if (!probs.length) empty("#c-dist", "No problems detected in this period.", "check");
  else {
    const main = probs.filter((r) => SLOTS.includes(r.label)), other = probs.filter((r) => !SLOTS.includes(r.label)).reduce((s, r) => s + r.n, 0);
    ringChart($("#c-dist"), { items: [...main.map((r) => ({ name: labelName(r.label), value: r.n, color: seriesColor(r.label) })),
      ...(other ? [{ name: "Other", value: other, color: "var(--s-other)" }] : [])], center: String(probs.reduce((s, r) => s + r.n, 0)), sub: "detections", caption: "Distribution of problems" });
  }

  // Fields
  if (!a.fields.length) empty("#c-fields", "No fields drawn yet.", "map");
  else $("#c-fields").innerHTML = `<div class="table-wrap"><table><thead><tr><th>Field</th><th>Health</th><th>Problems (last flight)</th><th>Last inspection</th></tr></thead><tbody>
      ${a.fields.map((f) => `<tr onclick="location.hash='#/map?field=${encodeURIComponent(f.id)}'"><td><strong>${esc(f.name)}</strong></td>
        <td>${f.health.pct != null ? `${gauge(f.health.pct, { size: 70, label: f.name })}` : `<span class="small muted">${esc(f.health.reason)}</span>`}</td>
        <td class="small">${Object.entries(f.problems).map(([l, n]) => `${esc(labelName(l))} ×${n}`).join("<br>") || "None"}</td>
        <td class="small mono">${f.last_inspection ? esc(dateTime(f.last_inspection.t)) : "Never"}</td></tr>`).join("")}</tbody></table></div>
    ${a.by_field.some((f) => f.total) ? `<h3 style="margin:14px 0 4px">All-time problems by field</h3><div id="c-byfield"></div>` : ""}`;
  if (a.by_field.some((f) => f.total)) hbarChart($("#c-byfield"), { items: a.by_field.map((f) => ({ name: f.name, value: f.total, color: "var(--s1)" })), caption: "Problems per field" });

  // Change between inspections
  const changes = a.fields.filter((f) => f.change);
  if (!changes.length) empty("#c-change", "Needs at least two inspection flights of the same field.", "history");
  else $("#c-change").innerHTML = changes.map((f) => {
    const c = f.change, dlt = c.health_delta;
    return `<div style="padding:10px 0;border-bottom:1px solid var(--line)"><div class="row"><strong>${esc(f.name)}</strong><span class="spacer"></span>
        ${dlt != null ? `<span class="tag ${dlt >= 0 ? "tag--ok" : "tag--bad"}">${dlt >= 0 ? "+" : ""}${fmt(dlt, 1)} pts health</span>` : `<span class="small muted">${esc(c.reason)}</span>`}</div>
      <p class="small muted">${esc(date(c.previous_t))} → ${esc(date(f.last_inspection.t))}</p>
      <ul class="small" style="margin:6px 0 0;padding-left:18px">${Object.entries(c.problems).map(([l, v]) => `<li>${esc(labelName(l))}: ${v.before} → ${v.now}</li>`).join("") || "<li>No problems in either flight</li>"}</ul></div>`;
  }).join("");

  // Heatmap: condition x field (sequential single hue: more = darker)
  const hf = a.by_field.filter((f) => f.total), condSet = [...new Set(hf.flatMap((f) => Object.keys(f.problems)))].sort();
  if (!hf.length) empty("#c-heat", "No problems with a field assigned yet.", "grid");
  else {
    const max = Math.max(...hf.flatMap((f) => Object.values(f.problems)));
    $("#c-heat").innerHTML = `<div class="table-wrap"><table><thead><tr><th>Condition</th>${hf.map((f) => `<th>${esc(f.name)}</th>`).join("")}</tr></thead><tbody>
      ${condSet.map((l) => `<tr style="cursor:default"><td>${esc(labelName(l))}</td>${hf.map((f) => { const n = f.problems[l] || 0;
        return `<td class="mono" style="text-align:center;background:color-mix(in srgb, var(--s2) ${n ? 15 + (75 * n) / max : 0}%, transparent);color:${n / max > 0.6 ? "#fff" : "inherit"}" title="${esc(labelName(l))} in ${esc(f.name)}: ${n}">${n || "·"}</td>`; }).join("")}</tr>`).join("")}
      </tbody></table></div><p class="small muted" style="margin-top:6px">Stronger colour = more detections of that condition in that field.</p>`;
  }

  // Attention areas
  if (!a.attention.length) empty("#c-att", "No clusters of problems with a GPS position in this period.", "check");
  else $("#c-att").innerHTML = `<ul class="alerts">${a.attention.slice(0, 8).map((x) => `<li data-level="warn">${ICON("pin")}
      <div><a href="#/map?id=${encodeURIComponent(x.ids[0])}" style="color:inherit"><strong>${esc(labelName(x.top_label))}</strong> · ${x.count} detection${x.count === 1 ? "" : "s"} within 10 m</a>
      <small class="mono">${x.lat.toFixed(5)}, ${x.lon.toFixed(5)}</small></div><time>${esc(date(x.last_t))}</time></li>`).join("")}</ul>`;

  // Missions
  if (!ms.per_mission.length) empty("#c-missions", "No inspection flights flown yet.", "drone");
  else {
    $("#c-missions").innerHTML = `<div id="c-mbar"></div><dl class="kv small" style="margin-top:10px"><dt>Planned / failed to start</dt><dd>${int(ms.by_status.planned || 0)} / ${int(ms.by_status.failed || 0)}</dd>
      <dt>Ended early</dt><dd>${int(ms.by_status.aborted || 0)}</dd><dt>Distance flown</dt><dd>${int(ms.distance_m)} m</dd></dl>`;
    barChart($("#c-mbar"), { categories: ms.per_mission.map((m) => date(m.t)), series: [
      { name: "Completed (min)", color: "var(--s3)", values: ms.per_mission.map((m) => (m.status === "completed" ? +(m.duration_s / 60).toFixed(1) : 0)) },
      { name: "Ended early (min)", color: "var(--s4)", values: ms.per_mission.map((m) => (m.status !== "completed" ? +((m.duration_s || 0) / 60).toFixed(1) : 0)) }],
      caption: "Flight duration per mission in minutes", height: 180, format: (v) => `${v} min` });
  }
  const onTheme = () => draw();
  window.addEventListener("themechange", onTheme);
  return () => window.removeEventListener("themechange", onTheme);
}
