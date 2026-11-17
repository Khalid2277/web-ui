// Farm map: fields (draw / edit / archive), inspected coverage, routes, disease markers, density, filters.
import { api, live } from "../api.js";
import { DEFAULT_CENTER, detectionMarker, droneIcon, fieldStyle, fitTo, makeMap, overlay } from "../mapkit.js";
import { metaRow, ICON, KIND_TEXT, area, confirmDialog, date, esc, fmt, kindMark, labelName, promptDialog, toast } from "../util.js";

const L = window.L;

export async function mount(root, { params }) {
  root.innerHTML = `
    <div class="page-head"><div><h1>Fields &amp; findings</h1>${metaRow()}
      <p>Field boundaries, where the drone has inspected, and every detection placed at the drone's GPS position when the photo was taken.</p></div>
      <div class="row"><button class="btn" id="b-draw">${ICON("plus")}Draw a field</button></div></div>
    <div class="map-layout">
      <aside class="stack">
        <section class="card"><div class="card__head"><h2>${ICON("map")}Fields</h2></div><ul class="side-list" id="fields"></ul><div id="field-detail"></div></section>
        <section class="card"><div class="card__head"><h2>${ICON("search")}Filter detections</h2></div>
          <div class="stack">
            <label class="field">Type<select id="f-kind"><option value="">All problems</option><option value="disease">Disease</option><option value="deficiency">Nutrient deficiency</option><option value="pest">Pest</option><option value="healthy">Healthy</option></select></label>
            <label class="field">Condition<select id="f-label"><option value="">Any</option></select></label>
            <label class="field">Period<select id="f-days"><option value="7">Last 7 days</option><option value="30" selected>Last 30 days</option><option value="90">Last 90 days</option><option value="">All time</option></select></label>
            <label class="field">Minimum confidence<select id="f-p"><option value="">Any</option><option value="0.7">70 %+</option><option value="0.85">85 %+</option><option value="0.95">95 %+</option></select></label>
            <label class="check"><input type="checkbox" id="f-unc"> Include uncertain results</label>
            <p class="small muted" id="f-count"></p>
          </div></section>
        <section class="card"><div class="card__head"><h2>${ICON("alert")}Areas needing attention</h2></div><ul class="side-list" id="attention"></ul></section>
      </aside>
      <section class="card card--flush" style="position:relative">
        <div class="map map--tall" id="map" style="border:0;border-radius:14px"></div>
        <div class="draw-hint glass" id="draw-hint" hidden>
          <p class="small" style="margin-bottom:8px"><strong>Drawing a field.</strong> Click the map to add corners (<span id="n-pts">0</span>).</p>
          <div class="row"><button class="btn btn--sm" id="d-done" disabled>${ICON("check")}Finish</button>
            <button class="btn btn--ghost btn--sm" id="d-undo">Undo</button><button class="btn btn--ghost btn--sm" id="d-cancel">Cancel</button></div></div>
      </section>
    </div>
    <div class="map-legend" style="margin-top:12px">
      ${["disease", "deficiency", "pest", "healthy", "uncertain"].map((k) => `<span class="kind">${kindMark(k)}${KIND_TEXT[k]}</span>`).join("")}
      <span><i style="display:inline-block;width:14px;height:14px;background:rgba(200,241,105,.35);vertical-align:-2px"></i> Inspected (latest flight)</span>
      <span><i style="display:inline-block;width:14px;height:14px;border:2px dashed #f4f1e6;background:#2a3a2f;vertical-align:-2px"></i> Field boundary</span>
      <span><i style="display:inline-block;width:14px;height:14px;border-radius:50%;background:rgba(230,103,103,.35);border:2px solid var(--k-disease);vertical-align:-2px"></i> Attention area (cluster)</span>
    </div>`;
  const $ = (s) => root.querySelector(s);
  const map = makeMap($("#map"), { zoom: 17 });
  const fieldsL = overlay(map, "Fields", L.featureGroup());
  const covL = overlay(map, "Inspected area", L.layerGroup());
  const routesL = overlay(map, "Flight tracks", L.layerGroup(), false);
  const detL = overlay(map, "Detections", L.layerGroup());
  const attL = overlay(map, "Attention areas", L.layerGroup());
  let data = null, fields = [], selected = params.field || null, markers = new Map(), fitted = false;

  // ---------------------------------------------------------------- data
  async function load() {
    const q = new URLSearchParams();
    const days = $("#f-days").value;
    if (days) q.set("from", String(Date.now() / 1000 - days * 86400));
    if ($("#f-kind").value) q.set("kind", $("#f-kind").value);
    if ($("#f-label").value) q.set("label", $("#f-label").value);
    if ($("#f-p").value) q.set("min_p", $("#f-p").value);
    try {
      [data, fields] = await Promise.all([api(`/api/map?${q}`), api("/api/fields")]);
    } catch (e) { toast(e.message, "error"); return; }
    render();
  }
  function render() {
    fieldsL.clearLayers(); covL.clearLayers(); routesL.clearLayers(); detL.clearLayers(); markers.clear();
    const names = Object.fromEntries(fields.map((f) => [f.id, f.name]));
    for (const f of data.fields) {
      L.polygon(f.polygon, fieldStyle(f.id === selected)).bindTooltip(f.name, { sticky: true })
        .on("click", () => selectField(f.id)).addTo(fieldsL);
      for (const cell of f.coverage_cells) L.polygon(cell, { stroke: false, fillColor: "#c8f169", fillOpacity: 0.3, interactive: false }).addTo(covL);
    }
    for (const r of data.routes) if (r.track.length > 1) L.polyline(r.track, { color: "#c8f169", weight: 2, opacity: 0.7 }).bindTooltip(`Mission ${r.id}`).addTo(routesL);
    const showUnc = $("#f-unc").checked, kind = $("#f-kind").value;
    const dets = data.detections.filter((d) => d.kind !== "none" && (showUnc || d.status !== "uncertain") && (kind || d.kind !== "healthy"));
    for (const d of dets) {
      const m = detectionMarker(d, names[d.field_id]).addTo(detL);
      markers.set(d.id, m);
      if (d.h_acc_m) m.on("popupopen", () => { m._acc = L.circle([d.lat, d.lon], { radius: d.h_acc_m, color: "#f4f1e6", weight: 1, fillOpacity: 0.08, dashArray: "3 3" }).addTo(map); })
        .on("popupclose", () => m._acc?.remove());
    }
    $("#f-count").textContent = `${dets.length} detection${dets.length === 1 ? "" : "s"} with a GPS position shown.`;
    const labels = [...new Set(data.detections.map((d) => d.label))].filter((l) => l !== "no_tomato_leaf").sort();
    const cur = $("#f-label").value;
    $("#f-label").innerHTML = `<option value="">Any</option>${labels.map((l) => `<option value="${esc(l)}" ${l === cur ? "selected" : ""}>${esc(labelName(l))}</option>`).join("")}`;
    renderFieldList();
    if (!fitted) {
      fitted = true;
      const focus = params.id && markers.get(params.id);
      if (focus) { map.setView(focus.getLatLng(), 19); focus.openPopup(); }
      else if (selected) selectField(selected);
      else if (!fitTo(map, [...data.fields.flatMap((f) => f.polygon), ...dets.map((d) => [d.lat, d.lon])])) {
        const h = data.home || live.state?.telemetry?.home;
        map.setView(h ? [h.lat, h.lon] : DEFAULT_CENTER, 17);
      }
    }
  }
  async function loadAttention() {
    const days = +$("#f-days").value || 365;
    const a = await api(`/api/analytics?days=${days}`).catch(() => null);
    attL.clearLayers();
    const list = a?.attention || [];
    $("#attention").innerHTML = list.length ? list.slice(0, 8).map((x, i) => `<li><button data-i="${i}">${kindMark("disease")}
        <span style="flex:1"><strong>${esc(labelName(x.top_label))}</strong><br><span class="small muted">${x.count} detection${x.count === 1 ? "" : "s"} · last ${esc(date(x.last_t))}</span></span></button></li>`).join("")
      : `<li><div class="empty" style="padding:12px">${ICON("check")}<span>No clusters of problems in this period.</span></div></li>`;
    list.forEach((x) => L.circle([x.lat, x.lon], { radius: 6 + 2 * Math.sqrt(x.count), color: "#e66767", weight: 2, fillOpacity: 0.25 })
      .bindTooltip(`${labelName(x.top_label)} · ${x.count}`).addTo(attL));
    $("#attention").onclick = (e) => { const b = e.target.closest("button[data-i]"); if (b) map.setView([list[b.dataset.i].lat, list[b.dataset.i].lon], 20); };
  }

  // ---------------------------------------------------------------- fields
  function renderFieldList() {
    $("#fields").innerHTML = fields.length ? fields.map((f) => `<li><button data-id="${esc(f.id)}" aria-pressed="${f.id === selected}">
        <span class="kind__mark" style="background:${f.id === selected ? "var(--accent)" : "var(--line-strong)"};border-radius:2px"></span>
        <span style="flex:1"><strong>${esc(f.name)}</strong><br><span class="small muted">${esc(area(f.area_m2))} · health ${f.summary.health.pct != null ? fmt(f.summary.health.pct, 0) + "%" : "n/a"}</span></span></button></li>`).join("")
      : `<li><div class="empty" style="padding:12px">${ICON("map")}<span>No fields yet. Use “Draw a field”.</span></div></li>`;
  }
  $("#fields").addEventListener("click", (e) => { const b = e.target.closest("button[data-id]"); if (b) selectField(b.dataset.id); });
  function selectField(id) {
    selected = id;
    const f = fields.find((x) => x.id === id);
    if (!f) return;
    fieldsL.eachLayer((l) => l.setStyle(fieldStyle(l.getTooltip()?.getContent() === f.name)));
    fitTo(map, f.polygon);
    renderFieldList();
    const s = f.summary, mf = data.fields.find((x) => x.id === id);
    const local = live.state?.local !== false;
    $("#field-detail").innerHTML = `<hr class="rule"><h3>${esc(f.name)}</h3>
      <dl class="kv small" style="margin-top:8px"><dt>Area</dt><dd>${esc(area(f.area_m2))}</dd><dt>Crop</dt><dd>${esc(f.crop)}</dd>
        <dt>Crop health</dt><dd>${s.health.pct != null ? fmt(s.health.pct, 0) + "%" : esc(s.health.reason)}</dd>
        <dt>Last inspection</dt><dd>${s.last_inspection ? `<a href="#/missions/${encodeURIComponent(s.last_inspection.mission_id)}">${esc(date(s.last_inspection.t))}</a>` : "Never"}</dd>
        <dt>Inspected</dt><dd>${mf?.coverage ? `${fmt(mf.coverage.pct, 0)}% of the field` : "—"}</dd></dl>
      ${mf?.coverage ? `<p class="small muted" style="margin-top:6px">Coverage estimate: ${esc(mf.coverage.assumption)}</p>` : ""}
      <div class="row" style="margin-top:10px">
        <a class="btn btn--sm" href="#/live">${ICON("route")}Plan inspection</a>
        <button class="btn btn--ghost btn--sm" id="fe-rename" ${local ? "" : "disabled"}>${ICON("edit")}Rename</button>
        <button class="btn btn--ghost btn--sm" id="fe-redraw" ${local ? "" : "disabled"}>${ICON("map")}Redraw</button>
        <button class="btn btn--ghost btn--sm" id="fe-del" ${local ? "" : "disabled"}>${ICON("trash")}Remove</button></div>`;
    $("#fe-rename").onclick = async () => {
      const name = await promptDialog({ title: "Rename field", label: "Field name", value: f.name });
      if (!name) return;
      try { await api(`/api/fields/${encodeURIComponent(id)}`, { method: "PATCH", body: { name } }); toast("Field renamed.", "ok"); await load(); selectField(id); }
      catch (e) { toast(e.message, "error"); }
    };
    $("#fe-redraw").onclick = () => startDraw(id);
    $("#fe-del").onclick = async () => {
      const hist = s.last_inspection;
      if (!(await confirmDialog({ title: `Remove “${f.name}”?`, body: hist
        ? "<p>This field has inspection history, so it will be <strong>archived</strong>: hidden from the map and planning, but its missions and detections are kept.</p>"
        : "<p>The field has no missions or detections and will be deleted.</p>", confirmText: hist ? "Archive field" : "Delete field", kind: "danger" }))) return;
      try { const r = await api(`/api/fields/${encodeURIComponent(id)}`, { method: "DELETE" }); toast(r.message || "Field deleted.", "ok"); selected = null; $("#field-detail").innerHTML = ""; await load(); }
      catch (e) { toast(e.message, "error"); }
    };
  }

  // ---------------------------------------------------------------- drawing
  let drawing = null;
  function startDraw(editId = null) {
    if (live.state?.local === false) { toast("Fields can only be changed from the drone's local network.", "warn"); return; }
    stopDraw();
    drawing = { pts: [], editId, line: L.polyline([], { color: "#c8f169", weight: 2, dashArray: "5 5" }).addTo(map), dots: L.layerGroup().addTo(map) };
    $("#draw-hint").hidden = false; $("#n-pts").textContent = "0"; $("#d-done").disabled = true;
    map.getContainer().style.cursor = "crosshair";
    map.doubleClickZoom.disable();
  }
  function stopDraw() {
    if (!drawing) return;
    drawing.line.remove(); drawing.dots.remove(); drawing = null;
    $("#draw-hint").hidden = true; map.getContainer().style.cursor = ""; map.doubleClickZoom.enable();
  }
  function redrawSketch() {
    const pts = drawing.pts;
    drawing.line.setLatLngs(pts.length > 2 ? [...pts, pts[0]] : pts);
    drawing.dots.clearLayers();
    pts.forEach((p) => L.circleMarker(p, { radius: 5, color: "#0c120e", fillColor: "#c8f169", fillOpacity: 1, weight: 2 }).addTo(drawing.dots));
    $("#n-pts").textContent = String(pts.length);
    $("#d-done").disabled = pts.length < 3;
  }
  map.on("click", (e) => { if (!drawing) return; drawing.pts.push([+e.latlng.lat.toFixed(7), +e.latlng.lng.toFixed(7)]); redrawSketch(); });
  $("#d-undo").onclick = () => { if (drawing) { drawing.pts.pop(); redrawSketch(); } };
  $("#d-cancel").onclick = stopDraw;
  $("#d-done").onclick = async () => {
    const { pts, editId } = drawing;
    try {
      if (editId) {
        await api(`/api/fields/${encodeURIComponent(editId)}`, { method: "PATCH", body: { polygon: pts } });
        toast("Boundary updated.", "ok");
      } else {
        const name = await promptDialog({ title: "Name this field", label: "Field name", value: `Field ${fields.length + 1}`, confirmText: "Save field" });
        if (!name) return;
        const f = await api("/api/fields", { method: "POST", body: { name, polygon: pts } });
        toast(`Saved ${f.name} (${area(f.area_m2)}).`, "ok");
        selected = f.id;
      }
      stopDraw();
      await load();
      if (selected) selectField(selected);
    } catch (e) { toast(e.message, "error", 8000); }
  };
  $("#b-draw").addEventListener("click", () => startDraw());

  // ---------------------------------------------------------------- live drone marker
  let drone = null;
  const unsub = live.subscribe((s) => {
    const t = s.telemetry || {};
    $("#b-draw").disabled = s.local === false;
    if (t.lat == null || !t.link) return;
    if (!drone) drone = L.marker([t.lat, t.lon], { icon: droneIcon(t.heading), zIndexOffset: 1000, title: "Drone" }).addTo(map);
    else { drone.setLatLng([t.lat, t.lon]); drone.setIcon(droneIcon(t.heading)); }
  });

  ["#f-kind", "#f-label", "#f-days", "#f-p"].forEach((id) => $(id).addEventListener("change", () => { load(); if (id === "#f-days") loadAttention(); }));
  $("#f-unc").addEventListener("change", render);
  if (params.id) $("#f-days").value = "";
  await load();
  loadAttention();
  if (params.draw) startDraw();
  return () => { unsub(); map.remove(); };
}
