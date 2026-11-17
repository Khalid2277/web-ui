"use strict";

const $ = (id) => document.getElementById(id);
const fmt = (v, digits = 1, dash = "—") => (v === null || v === undefined || Number.isNaN(v) ? dash : Number(v).toFixed(digits));
const timeOf = (t) => new Date(t * 1000).toLocaleTimeString([], { hour12: false });
const ICON = (name, cls = "icon icon--sm") => `<svg class="${cls}" aria-hidden="true"><use href="#i-${name}"/></svg>`;

/* "Tomato___Late_blight" (baseline) or "late_blight" (v2) -> display parts.
   kind: "disease" | "healthy" | "none" (no tomato leaf) */
const NICE = { yellow_leaf_curl_virus: "Yellow leaf curl virus", septoria_leaf_spot: "Septoria leaf spot",
  spider_mites: "Spider mites", no_tomato_leaf: "No tomato leaf" };
function parseLabel(raw) {
  const parts = raw.split("___");
  const crop = parts.length > 1 ? parts[0].replace(/_/g, " ") : "Tomato";
  const cond = parts.length > 1 ? parts[1] : raw;
  const name = NICE[cond] || cond.replace(/_/g, " ").replace(/\s+/g, " ").trim().replace(/^./, (c) => c.toUpperCase());
  const kind = cond === "no_tomato_leaf" ? "none" : /healthy/i.test(cond) ? "healthy" : "disease";
  // What kind of problem, so the verdict points to the right action (fungicide vs fertiliser vs pest control).
  const problem = /deficiency/i.test(cond) ? "deficiency" : /leaf_miner|spider_mites/i.test(cond) ? "pest" : "disease";
  return { crop, name, kind, problem, healthy: kind === "healthy" };
}

/* ---------------------------------------------------------------- clock */
setInterval(() => { $("clock").textContent = new Date().toLocaleTimeString([], { hour12: false }); }, 1000);

/* ---------------------------------------------------------------- sparklines */
let paused = false;
$("pause-btn").addEventListener("click", (e) => {
  paused = !paused;
  const btn = e.currentTarget;
  btn.setAttribute("aria-pressed", String(paused));
  btn.innerHTML = paused ? `${ICON("play", "icon")}<span>Resume charts</span>` : `${ICON("pause", "icon")}<span>Pause charts</span>`;
});

function spark(canvas, points, color) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (canvas.width !== w * dpr) { canvas.width = w * dpr; canvas.height = h * dpr; }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  ctx.strokeStyle = "#d6d1c1"; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(0, h - 0.5); ctx.lineTo(w, h - 0.5); ctx.stroke();
  const vals = points.filter((v) => v !== null && v !== undefined);
  if (vals.length < 2) return;
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (hi - lo < 0.2) { const mid = (hi + lo) / 2; lo = mid - 0.1; hi = mid + 0.1; }
  const x = (i) => (i / (points.length - 1)) * w;
  const y = (v) => h - 4 - ((v - lo) / (hi - lo)) * (h - 8);
  ctx.beginPath();
  points.forEach((v, i) => { if (v !== null && v !== undefined) (i ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v))); });
  ctx.strokeStyle = color; ctx.lineWidth = 1.75; ctx.lineJoin = "round"; ctx.stroke();
  ctx.lineTo(w, h); ctx.lineTo(0, h); ctx.closePath();
  ctx.globalAlpha = 0.08; ctx.fillStyle = color; ctx.fill(); ctx.globalAlpha = 1;
}

/* ---------------------------------------------------------------- map */
let map = null, droneMarker = null, centred = false;
const detectionLayer = [];

function initMap() {
  const el = $("map");
  if (!window.L) {
    el.innerHTML = `<div class="empty">${ICON("pin", "icon icon--lg")}<p><strong>Map tiles unavailable</strong></p>
      <p>The viewing device has no internet access. Positions are still recorded in the detection log.</p></div>`;
    return;
  }
  map = L.map(el, { zoomControl: true, attributionControl: true }).setView([25.3094, 55.4861], 16);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 20, maxNativeZoom: 19, attribution: "© OpenStreetMap contributors",
  }).addTo(map);
}

function markerIcon(kind) {
  return L.divIcon({ className: "", html: `<span class="mk mk--${kind}" style="width:16px;height:16px"></span>`, iconSize: [16, 16], iconAnchor: [8, 8] });
}

function plotDetection(d) {
  if (!map || d.lat == null || d.lon == null || parseLabel(d.label).kind === "none") return;
  const lab = parseLabel(d.label);
  const m = L.marker([d.lat, d.lon], { icon: markerIcon(lab.healthy ? "healthy" : "disease"), keyboard: true, title: lab.name })
    .bindPopup(`<strong>${lab.name}</strong><br>${Math.round(d.p * 100)}% · ${timeOf(d.t)}`);
  m.addTo(map); detectionLayer.push(m);
}

/* ---------------------------------------------------------------- detection log */
function renderLogItem(d) {
  const lab = parseLabel(d.label);
  const kind = d.status === "uncertain" ? "uncertain" : lab.kind;
  const [icon, text] = { disease: ["alert", { deficiency: "Nutrient deficiency suspected", pest: "Pest damage suspected" }[lab.problem] || "Disease suspected"],
    healthy: ["check", "Healthy"], none: ["minus", "No tomato leaf"], uncertain: ["alert", "Uncertain"] }[kind];
  const pos = d.lat != null ? `${d.lat.toFixed(6)}, ${d.lon.toFixed(6)}` : "no GPS fix";
  const leaves = d.leaves && d.leaves.length ? `<span>${d.leaves.length} lea${d.leaves.length === 1 ? "f" : "ves"}</span>` : "";
  const test = d.truth ? `<span>test: ${parseLabel(d.truth).name} ${d.truth === d.label ? "✓" : "✗"}</span>` : "";
  const li = document.createElement("li");
  li.innerHTML = `<a href="/captures/${d.image}" target="_blank" rel="noopener">
      <img src="/captures/${d.thumb}" width="112" height="63" loading="lazy" alt="Frame logged at ${timeOf(d.t)}"></a>
    <div><p class="log__label">${kind === "none" ? "No tomato leaf" : `${lab.crop} · ${lab.name}`}</p>
      <p class="log__kind" data-kind="${kind === "healthy" ? "healthy" : kind === "disease" ? "disease" : ""}">${ICON(icon)}${text} · ${Math.round(d.p * 100)}%</p>
      <p class="log__meta mono"><time>${timeOf(d.t)}</time><span>${pos}</span>${d.alt_rel != null ? `<span>${fmt(d.alt_rel)} m</span>` : ""}${leaves}${test}</p></div>`;
  return li;
}

function setLog(entries) {
  const list = $("log-list");
  list.replaceChildren(...entries.map(renderLogItem));
  $("log-empty").hidden = entries.length > 0;
  $("log-count").textContent = `${entries.length} record${entries.length === 1 ? "" : "s"}`;
  entries.forEach(plotDetection);
}

async function loadLog() {
  try { setLog(await (await fetch("/api/detections")).json()); } catch { /* shown as empty */ }
}

$("snap-btn").addEventListener("click", async (e) => {
  const btn = e.currentTarget, msg = $("snap-msg");
  btn.disabled = true; msg.dataset.kind = ""; msg.textContent = "Capturing full-resolution frame…";
  try {
    const truth = $("truth").value || null, item = $("truth-item").value.trim() || null;
    const res = await fetch("/api/snapshot", { method: "POST", headers: { "Content-Type": "application/json" },
                                               body: JSON.stringify({ truth, item }) });
    const body = await res.json();
    if (!res.ok) throw new Error(body.error || `Capture failed (${res.status}).`);
    const list = $("log-list");
    list.prepend(renderLogItem(body));
    $("log-empty").hidden = true;
    const n = list.children.length;
    $("log-count").textContent = `${n} record${n === 1 ? "" : "s"}`;
    plotDetection(body);
    msg.dataset.kind = "ok";
    const verdict = body.truth ? (body.truth === body.label ? " ✓ matches the test label" : ` ✗ test label was ${parseLabel(body.truth).name}`) : "";
    msg.textContent = `Logged: ${parseLabel(body.label).name} (${Math.round(body.p * 100)}%)${body.lat == null ? ", without GPS position" : ""}.${verdict}`;
  } catch (err) {
    msg.dataset.kind = "error";
    msg.textContent = `${err.message} Try again in a few seconds.`;
  } finally {
    btn.disabled = false;
    setTimeout(() => { if (msg.dataset.kind === "ok") msg.textContent = ""; }, 5000);
  }
});

/* ---------------------------------------------------------------- live state */
function setCheck(id, ok, label) {
  const li = $(id);
  li.dataset.ok = ok === null ? "unknown" : ok ? "yes" : "no";
  li.innerHTML = `${ICON(ok === null ? "minus" : ok ? "check" : "alert")}${label}`;
}

function setStage(stage, s, text) {
  const el = document.querySelector(`[data-stage="${stage}"] .state`);
  el.dataset.s = s; el.textContent = text;
}

/* Local network viewers get the full 20 fps; viewers over the public tunnel get 5 fps. */
const REMOTE = !/^(localhost|raspberrypi\.local|\d+\.\d+\.\d+\.\d+)$/.test(location.hostname);
const streamUrl = () => `/stream.mjpg?fps=${REMOTE ? 5 : 20}&t=${Date.now()}`;
$("feed-img").src = streamUrl();

let camWasLive = null;
function renderCamera(cam) {
  const live = cam.status === "live";
  $("cam-tag").textContent = live ? `Camera · live` : "Camera · offline";
  $("cam-tag").className = `tag mono ${live ? "tag--ok" : "tag--bad"}`;
  $("hud-res").textContent = `${cam.width}×${cam.height}`;
  $("hud-fps").textContent = `${fmt(cam.fps, 0)} fps`;
  $("hud-time").textContent = new Date().toLocaleTimeString([], { hour12: false });
  $("feed-empty").hidden = live;
  if (!live) $("feed-reason").textContent = cam.status.replace(/^offline:\s*/, "") || "Waiting for frames.";
  if (live && camWasLive === false) $("feed-img").src = streamUrl(); // reconnect stream
  camWasLive = live;
  setCheck("l-cam", live, "Camera");
  setStage("imaging", live ? "live" : "down", live ? "Live" : "Offline");
}

/* kind used for colour + icon; uncertain readings are never shown as a confident disease */
const kindOf = (item) => (item.status === "uncertain" ? "uncertain" : parseLabel(item.label).kind);
const VERDICT = {
  disease: ["alert", "Disease suspected"], healthy: ["check", "Healthy"],
  none: ["minus", "No tomato leaf in view"], uncertain: ["alert", "Uncertain: get closer or re-check"],
  deficiency: ["alert", "Nutrient deficiency suspected"], pest: ["alert", "Pest damage suspected"],
};

function renderBoxes(leaves) {
  $("feed-boxes").innerHTML = leaves.map((l, i) => {
    const [x0, y0, x1, y1] = l.box, k = kindOf(l), lab = parseLabel(l.label);
    return `<div class="box" data-kind="${k}" style="left:${x0 * 100}%;top:${y0 * 100}%;width:${(x1 - x0) * 100}%;height:${(y1 - y0) * 100}%">
      <span class="box__label">${i + 1} · ${k === "uncertain" ? "?" : lab.name} ${Math.round(l.p * 100)}%</span></div>`;
  }).join("");
}

function renderAI(ai) {
  if (!ai) {
    $("ai-label").textContent = "Waiting for a camera frame";
    $("ai-crop").textContent = "—";
    renderBoxes([]);
    setStage("ai", "degraded", "Idle");
    return;
  }
  const best = ai.headline, lab = parseLabel(best.label), pct = Math.round(best.p * 100);
  const kind = kindOf(best);
  $("ai-crop").textContent = kind === "none" ? "Whole frame"
    : kind === "uncertain" ? `Possibly ${lab.name.toLowerCase()}` : ai.leaves.length ? `${lab.crop} · leaf` : lab.crop;
  $("ai-label").textContent = kind === "none" ? "Nothing to diagnose" : kind === "uncertain" ? "Uncertain" : lab.name;
  const v = $("ai-verdict");
  const [icon, text] = kind === "disease" ? VERDICT[lab.problem] || VERDICT.disease : VERDICT[kind];
  v.dataset.kind = kind === "disease" ? "disease" : kind === "healthy" ? "healthy" : "none";
  v.innerHTML = `${ICON(icon, "icon")}<span>${text}</span>`;
  $("ai-pct").textContent = `${pct}%`;
  $("ai-fill").style.transform = `scaleX(${best.p})`;
  $("ai-meter").dataset.kind = kind === "disease" ? "disease" : "";
  $("ai-meter").setAttribute("aria-valuenow", String(pct));
  $("ai-ranking").innerHTML = (best.top || []).slice(1).map((r) => {
    const l = parseLabel(r.label);
    return `<li><span>${l.name}</span><span class="mono">${Math.round(r.p * 100)}%</span><span class="bar"><i style="transform:scaleX(${r.p})"></i></span></li>`;
  }).join("");
  const env = $("ai-env");
  env.hidden = !(best.env_notes && best.env_notes.length);
  if (!env.hidden) env.textContent = `Field conditions raised this reading (camera alone: ${Math.round(best.p_image * 100)}%). ${best.env_notes[0].split(": ").slice(1).join(": ")}`;

  const leaves = ai.leaves || [];
  $("ai-leaves-wrap").hidden = leaves.length === 0;
  $("ai-leaves-title").textContent = `${leaves.length} lea${leaves.length === 1 ? "f" : "ves"} in view`;
  $("ai-leaves").innerHTML = leaves.map((l, i) => {
    const k = kindOf(l), name = k === "uncertain" ? `Uncertain (maybe ${parseLabel(l.label).name.toLowerCase()})` : parseLabel(l.label).name;
    return `<li data-kind="${k}"><span class="n">${i + 1}</span><span>${name}</span><span class="mono">${Math.round(l.p * 100)}%</span></li>`;
  }).join("");
  renderBoxes(leaves);

  const det = ai.ms_detect != null ? ` (detector ${ai.ms_detect} ms)` : "";
  $("ai-meta").textContent = `${ai.ms} ms per frame${det} · updated ${timeOf(ai.t)}`;
  $("s-infer").textContent = `${ai.ms} ms / frame`;
  setStage("ai", "live", "Running");
}

/* ---------------------------------------------------------------- model card + test mode */
let truthFilled = false;
function renderModel(model) {
  const c = (model && model.card) || {};
  const v2 = c.name && c.name !== "baseline";
  $("model-tag").textContent = v2 ? `${c.name} · ${c.version}` : "Baseline model";
  $("model-tag").className = `tag mono ${v2 ? "tag--ok" : "tag--warn"}`;
  $("mc-name").textContent = v2 ? `${c.name} (${c.version})` : "Baseline (PlantVillage only)";
  $("mc-arch").textContent = [c.architecture, c.detector].filter(Boolean).join(" + ") || "—";
  $("mc-f1").textContent = c.field_macro_f1 != null ? `${(c.field_macro_f1 * 100).toFixed(1)}% (baseline 23.1%)` : "Not measured";
  $("mc-fd").textContent = c.false_disease_rate != null ? `${(c.false_disease_rate * 100).toFixed(1)}% (baseline 99.1%)` : "Not measured";
  const classes = (c.classes || model.classes || []).filter((k) => k !== "no_tomato_leaf");
  $("mc-classes").textContent = classes.map((k) => parseLabel(k).name + ((c.lab_only_classes || []).includes(k) ? " (lab data only)" : "")).join(", ");
  $("stage-ai-desc").textContent = v2 ? "Leaf detector + per-leaf disease classifier on the Pi" : "Single-frame baseline classifier on the Pi";
  if (!truthFilled && (model.classes || []).length) {
    $("truth").insertAdjacentHTML("beforeend", model.classes.map((k) => `<option value="${k}">${parseLabel(k).name}</option>`).join(""));
    truthFilled = true;
  }
}

/* ---------------------------------------------------------------- field conditions */
function renderEnv(env) {
  const has = env && env.readings > 0 && env.latest;
  $("env-live").hidden = !has;
  $("env-empty").hidden = !!has;
  const fresh = has && Date.now() / 1000 - env.last_t < 900;
  $("env-tag").textContent = !has ? "No sensor data" : fresh ? `Live · ${env.readings} readings` : `Last reading ${timeOf(env.last_t)}`;
  $("env-tag").className = `tag mono ${fresh ? "tag--ok" : ""}`;
  setStage("lora", fresh ? "live" : has ? "degraded" : "planned", fresh ? "Live" : has ? "Stale" : "Planned");
  if (!has) return;
  const L = env.latest;
  $("e-temp").textContent = fmt(L.temp_c, 1);
  $("e-rh").textContent = fmt(L.rh, 0);
  $("e-soil").textContent = fmt(L.soil_moisture_pct, 0);
  $("e-press").textContent = fmt(L.pressure_hpa, 0);
  $("e-gas").textContent = L.gas_ohm != null ? fmt(L.gas_ohm / 1000, 1) : "—";
  $("e-soil-note").textContent = env.soil_note || "";
  if (!paused) {
    spark($("spark-env"), (env.series || []).map((s) => s[2]), "#1e5a43");
  }
  const d = env.diseases || {};
  const names = Object.keys(d).sort((a, b) => d[b].risk - d[a].risk);
  $("e-risks").innerHTML = names.length ? names.map((k) => {
    const r = d[k].risk, level = r >= 0.66 ? "high" : r >= 0.33 ? "medium" : "low";
    return `<li data-level="${level}"><span>${parseLabel(k).name}</span><span class="bar"><i style="transform:scaleX(${r})"></i></span>
      <span class="mono">${level}</span><span class="why">${d[k].why}</span></li>`;
  }).join("") : `<li class="muted">Collecting data: risk appears after 6 hours of readings (${env.hours_of_data || 0} h so far).</li>`;
}

const FIX = ["No GPS", "No fix", "2D fix", "3D fix", "DGPS", "RTK float", "RTK fixed", "Static", "PPP"];
function renderTelemetry(t) {
  const link = !!t.link;
  $("link-tag").textContent = link ? "Pixhawk · connected" : "Pixhawk · no link";
  $("link-tag").className = `tag mono ${link ? "tag--ok" : "tag--bad"}`;
  setCheck("l-mav", link, "MAVLink");
  setCheck("l-rc", link ? !!t.rc_ok : null, "RC transmitter");

  $("t-mode").textContent = link ? (t.mode || "—").replace(/_/g, " ") : "—";
  const armed = $("t-armed");
  armed.dataset.armed = !link ? "unknown" : t.armed ? "armed" : "disarmed";
  armed.innerHTML = !link ? `${ICON("minus")}<span>Unknown</span>`
    : t.armed ? `${ICON("alert")}<span>Armed: motors live</span>` : `${ICON("check")}<span>Disarmed</span>`;
  $("t-prearm").textContent = link ? `Pre-arm checks ${t.prearm_ok ? "passing" : "not passing"}` : "Pre-arm checks —";

  $("t-batt").textContent = link ? fmt(t.battery_v, 2) : "—";
  $("t-batt-sub").textContent = `${fmt(t.battery_a, 1)} A · ${t.battery_pct ?? "—"}%`;
  $("t-alt").textContent = link ? fmt(t.alt_rel, 1) : "—";
  $("t-alt-sub").textContent = `climb ${fmt(t.climb, 1)} m/s · ${fmt(t.groundspeed, 1)} m/s ground`;

  const fix = t.gps_fix ?? null;
  $("t-gps").textContent = fix === null || !link ? "—" : FIX[fix] || `Fix ${fix}`;
  $("t-gps-sub").textContent = `${t.gps_sats ?? "—"} satellites · HDOP ${t.gps_hdop ?? "—"}`;
  const hasFix = link && fix >= 3 && t.lat;
  $("t-pos").textContent = hasFix ? `${t.lat.toFixed(6)}, ${t.lon.toFixed(6)}` : "Position unavailable";
  $("map-tag").textContent = hasFix ? `${FIX[fix]} · ${t.gps_sats} sats` : "No GPS fix";
  $("map-tag").className = `tag mono ${hasFix ? "tag--ok" : ""}`;
  setStage("gps", hasFix ? "live" : link ? "degraded" : "down", hasFix ? "Locked" : link ? "No fix" : "No link");

  if (link && t.roll !== undefined) {
    const pitchPx = Math.max(-40, Math.min(40, t.pitch * 1.2));
    $("hz-world").setAttribute("transform", `rotate(${-t.roll} 60 60) translate(0 ${pitchPx})`);
    $("t-att").textContent = `R ${fmt(t.roll, 0)}° · P ${fmt(t.pitch, 0)}° · Y ${fmt((t.yaw + 360) % 360, 0)}°`;
  }

  if (!paused && t.history) {
    spark($("spark-batt"), t.history.map((h) => h[1]), "#1e5a43");
    spark($("spark-alt"), t.history.map((h) => h[2]), "#7a4f0d");
  }

  const msgs = t.messages || [];
  $("msg-count").textContent = `(${msgs.length})`;
  $("msg-list").innerHTML = msgs.length
    ? msgs.map((m) => `<li><time>${timeOf(m.t)}</time><span>${m.text.replace(/[<>&]/g, "")}</span></li>`).join("")
    : `<li class="muted">No messages yet.</li>`;

  if (map && hasFix) {
    const ll = [t.lat, t.lon];
    if (!droneMarker) droneMarker = L.marker(ll, { icon: markerIcon("drone"), title: "Drone", zIndexOffset: 1000 }).addTo(map);
    else droneMarker.setLatLng(ll);
    if (!centred) { map.setView(ll, 18); centred = true; }
  }
}

function renderSystem(s) {
  if (!s || !Object.keys(s).length) return;
  $("host").textContent = `${s.hostname || "raspberrypi"} · ${s.ip || ""}`;
  const temp = $("s-temp");
  temp.textContent = `${fmt(s.cpu_temp)} °C`;
  temp.dataset.kind = s.cpu_temp > 75 ? "bad" : "";
  $("s-load").textContent = `${fmt(s.load, 2)} of ${s.cores} cores`;
  $("s-mem").textContent = `${s.mem_pct}%`;
  $("s-disk").textContent = `${s.disk_pct}%`;
  const p = $("s-power");
  p.dataset.kind = s.undervoltage_now ? "bad" : s.undervoltage_since_boot ? "" : "ok";
  p.innerHTML = s.undervoltage_now ? `${ICON("alert")} Undervoltage now`
    : s.undervoltage_since_boot ? `${ICON("alert")} Dipped since boot` : `${ICON("check")} Stable`;
  $("s-wifi").textContent = s.wifi_ssid ? `${s.wifi_ssid} · ${s.wifi_signal}%` : "Not connected";
  const pub = $("s-public");
  if (s.public_url && pub.dataset.url !== s.public_url) {
    pub.dataset.url = s.public_url;
    pub.innerHTML = `<a href="${s.public_url}" target="_blank" rel="noopener">${s.public_url.replace("https://", "")}</a>`;
  } else if (!s.public_url) { pub.dataset.url = ""; pub.textContent = "Tunnel off"; }
  const h = Math.floor(s.uptime_s / 3600), m = Math.floor((s.uptime_s % 3600) / 60);
  $("s-up").textContent = `${h} h ${String(m).padStart(2, "0")} min`;
}

function render(state) {
  renderCamera(state.camera);
  renderModel(state.model || {});
  renderAI(state.ai);
  renderEnv(state.env);
  renderTelemetry(state.telemetry || {});
  renderSystem(state.system);
}

/* ---------------------------------------------------------------- connection */
function setConn(stateName, text) {
  const el = $("conn");
  el.dataset.state = stateName;
  el.querySelector(".status__text").textContent = text;
}

function connect() {
  const es = new EventSource("/events");
  es.onopen = () => setConn("live", "Live");
  es.onmessage = (e) => { try { render(JSON.parse(e.data)); } catch (err) { console.error(err); } };
  es.onerror = () => { setConn("lost", "Reconnecting"); };
}

window.addEventListener("DOMContentLoaded", () => {
  initMap();
  loadLog();
  connect();
});
