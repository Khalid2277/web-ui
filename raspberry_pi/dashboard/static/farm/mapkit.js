// Leaflet helpers shared by the map, operations and mission pages.
import { ICON, KIND_TEXT, clock, dateTime, esc, fmt, kindOf, labelName, latlon, pct } from "./util.js";

const L = window.L;
export const DEFAULT_CENTER = [25.3094, 55.4861];

export function makeMap(el, { center = DEFAULT_CENTER, zoom = 17, layersControl = true } = {}) {
  if (!L) {
    el.innerHTML = `<div class="empty">${ICON("map", "icon icon--lg")}<strong>Map library failed to load</strong></div>`;
    return null;
  }
  const map = L.map(el, { zoomControl: true, attributionControl: true, maxZoom: 21 }).setView(center, zoom);
  const sat = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
    maxZoom: 21, maxNativeZoom: 19, attribution: "Imagery © Esri, Maxar, Earthstar Geographics",
  });
  const osm = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 21, maxNativeZoom: 19, attribution: "© OpenStreetMap contributors",
  });
  sat.addTo(map);
  const base = { Satellite: sat, Streets: osm };
  map._base = base;
  if (layersControl) map._layers_ctl = L.control.layers(base, {}, { collapsed: true, position: "topright" }).addTo(map);
  // Tiles need internet; the farm data does not. Say so instead of showing grey squares.
  let errors = 0, notice = null;
  const onErr = () => {
    if (++errors < 4 || notice) return;
    notice = document.createElement("div");
    notice.className = "map-offline glass";
    notice.innerHTML = `${ICON("alert")} <strong>Map imagery unavailable.</strong> This device has no internet access, so background tiles cannot load. Fields, routes and detections are still shown.`;
    el.append(notice);
  };
  sat.on("tileerror", onErr); osm.on("tileerror", onErr);
  const onOk = () => { if (notice) { notice.remove(); notice = null; errors = 0; } };
  sat.on("tileload", onOk); osm.on("tileload", onOk);
  // Leaflet needs a size recalculation when its container appears after layout.
  const ro = new ResizeObserver(() => { if (map._loaded && map._mapPane) map.invalidateSize(); });
  ro.observe(el);
  map.on("unload", () => ro.disconnect());
  return map;
}

export function overlay(map, name, layer, on = true) {
  if (on) layer.addTo(map);
  map._layers_ctl?.addOverlay(layer, name);
  return layer;
}

export function markerIcon(kind, size = 16) {
  return L.divIcon({ className: "", html: `<span class="mk mk--${kind}" style="width:${size}px;height:${size}px"></span>`,
    iconSize: [size, size], iconAnchor: [size / 2, size / 2], popupAnchor: [0, -size / 2] });
}

export function droneIcon(heading = 0) {
  return L.divIcon({ className: "drone-icon", iconSize: [30, 30], iconAnchor: [15, 15],
    html: `<svg viewBox="0 0 30 30" style="transform:rotate(${heading || 0}deg)"><circle cx="15" cy="15" r="13" fill="var(--accent)" fill-opacity=".25" stroke="var(--accent)" stroke-width="2"/>
      <path d="M15 5 21 21 15 17 9 21z" fill="var(--accent)" stroke="#0c120e" stroke-width="1.2" stroke-linejoin="round"/></svg>` });
}

export const homeIcon = () => L.divIcon({ className: "", iconSize: [22, 22], iconAnchor: [11, 11],
  html: `<span style="display:grid;place-items:center;width:22px;height:22px;border-radius:5px;background:rgba(12,22,15,.75);border:2px solid #fff;color:#fff;font:700 11px 'IBM Plex Mono'">H</span>` });

export const fieldStyle = (selected = false) => ({ color: selected ? "#d5f26c" : "#ffffff", weight: selected ? 3 : 2,
  fillColor: "#d5f26c", fillOpacity: selected ? 0.12 : 0.05, dashArray: selected ? null : "6 5" });

export function detectionPopup(d, fieldName) {
  const k = kindOf(d.label, d.status);
  const acc = d.h_acc_m != null ? ` ± ${fmt(d.h_acc_m, 1)} m` : "";
  return `${d.thumb ? `<img src="/captures/${encodeURI(d.thumb)}" alt="Capture of ${esc(labelName(d.label))}" width="320" height="180" loading="lazy">` : ""}
    <strong style="font-size:1rem">${esc(labelName(d.label))}</strong>
    <div class="muted small">${esc(KIND_TEXT[k])}${d.status === "uncertain" ? " — below the confidence threshold" : ""}</div>
    <dl class="kv small" style="margin-top:8px">
      <dt>Confidence</dt><dd>${pct(d.p)}</dd>
      <dt>Severity</dt><dd>Not assessed by the model</dd>
      <dt>Position</dt><dd>${esc(latlon(d.lat, d.lon))}${esc(acc)}</dd>
      <dt>Time</dt><dd>${esc(dateTime(d.t))}</dd>
      <dt>Field</dt><dd>${esc(fieldName || "—")}</dd>
      <dt>Mission</dt><dd>${d.mission_id ? `<a href="#/missions/${encodeURIComponent(d.mission_id)}">${esc(d.mission_id)}</a>` : "Manual snapshot"}</dd>
      <dt>Leaves</dt><dd>${d.n_leaves ?? 0}</dd>
    </dl>
    ${d.guidance ? `<div class="guidance" style="margin-top:8px">${esc(d.guidance)}<small>General guidance — confirm with an agronomist.</small></div>` : ""}
    <p class="small muted" style="margin-top:6px">Position is the drone's GPS at capture time, not the exact leaf.</p>
    <a href="#/log?id=${encodeURIComponent(d.id)}">Open in disease log</a>`;
}

export function detectionMarker(d, fieldName) {
  const k = kindOf(d.label, d.status);
  return L.marker([d.lat, d.lon], { icon: markerIcon(k), title: `${labelName(d.label)} ${pct(d.p)} ${clock(d.t)}`, keyboard: true })
    .bindPopup(() => detectionPopup(d, fieldName), { maxWidth: 340 });
}

export function fitTo(map, latlngs, maxZoom = 19) {
  const pts = latlngs.filter((p) => p && Number.isFinite(p[0]));
  if (!map || !pts.length) return false;
  map.fitBounds(L.latLngBounds(pts), { padding: [30, 30], maxZoom });
  return true;
}
