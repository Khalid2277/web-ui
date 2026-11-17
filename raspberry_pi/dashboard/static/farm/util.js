// Shared helpers: escaping, formatting, labels, icons, dialogs, toasts.

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ESC[c]);

export const ICON = (name, cls = "icon icon--sm") => `<svg class="${cls}" aria-hidden="true"><use href="#i-${name}"/></svg>`;

export const isNum = (v) => typeof v === "number" && Number.isFinite(v);
export const fmt = (v, d = 1, dash = "—") => (isNum(v) ? v.toFixed(d) : dash);
export const pct = (p, d = 0) => (isNum(p) ? `${(p * 100).toFixed(d)}%` : "—");
export const int = (v) => (isNum(v) ? Math.round(v).toLocaleString() : "—");

export function area(m2) {
  if (!isNum(m2)) return "—";
  return m2 >= 10000 ? `${(m2 / 10000).toFixed(2)} ha` : `${Math.round(m2).toLocaleString()} m²`;
}
export function dur(s) {
  if (!isNum(s)) return "—";
  s = Math.max(0, Math.round(s));
  const m = Math.floor(s / 60), sec = s % 60;
  return m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : m ? `${m} min ${String(sec).padStart(2, "0")} s` : `${sec} s`;
}
export const dateTime = (t) => (isNum(t) ? new Date(t * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "—");
export const date = (t) => (isNum(t) ? new Date(t * 1000).toLocaleDateString([], { day: "numeric", month: "short" }) : "—");
export const clock = (t) => (isNum(t) ? new Date(t * 1000).toLocaleTimeString([], { hour12: false }) : "—");
export function ago(t) {
  if (!isNum(t)) return "never";
  const s = Date.now() / 1000 - t;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}
export const latlon = (la, lo) => (isNum(la) && isNum(lo) ? `${la.toFixed(5)}, ${lo.toFixed(5)}` : "No GPS position");

// ---------------------------------------------------------------- labels
const NICE = {
  yellow_leaf_curl_virus: "Yellow leaf curl virus", septoria_leaf_spot: "Septoria leaf spot", spider_mites: "Spider mites",
  no_tomato_leaf: "No tomato leaf", spotted_wilt_virus: "Spotted wilt virus", leaf_mold: "Leaf mould",
};
export function labelName(raw) {
  if (!raw) return "—";
  const cond = raw.includes("___") ? raw.split("___")[1] : raw;
  return NICE[cond] || cond.replace(/_/g, " ").trim().replace(/^./, (c) => c.toUpperCase());
}
export function kindOf(label, status) {
  if (status === "uncertain") return "uncertain";
  if (!label || label === "no_tomato_leaf") return "none";
  if (/healthy/i.test(label)) return "healthy";
  if (/deficiency/.test(label)) return "deficiency";
  if (label === "leaf_miner" || label === "spider_mites") return "pest";
  return "disease";
}
export const KIND_TEXT = { disease: "Disease", deficiency: "Nutrient deficiency", pest: "Pest", healthy: "Healthy",
  uncertain: "Uncertain", none: "No leaf" };
export const kindMark = (k) => `<span class="kind__mark" data-kind="${k}" aria-hidden="true"></span>`;
export const kindTag = (label, status) => {
  const k = kindOf(label, status);
  return `<span class="kind">${kindMark(k)}<span>${esc(labelName(label))}</span></span>`;
};

// Fixed categorical slots for problem labels (entity -> colour never changes with filters).
const SLOT_ORDER = ["early_blight", "late_blight", "bacterial_spot", "septoria_leaf_spot", "leaf_mold",
  "yellow_leaf_curl_virus", "target_spot", "leaf_miner"];
export const seriesColor = (label) => {
  const i = SLOT_ORDER.indexOf(label);
  return i >= 0 ? `var(--s${i + 1})` : "var(--s-other)";
};

// ---------------------------------------------------------------- element helper
export function html(strings, ...vals) {
  return strings.reduce((out, s, i) => out + s + (i < vals.length ? (vals[i]?.__html ?? esc(vals[i])) : ""), "");
}
export const raw = (s) => ({ __html: s });

// ---------------------------------------------------------------- toasts
export function toast(text, kind = "info", ms = 5000) {
  const el = document.createElement("div");
  el.className = "toast";
  el.dataset.kind = kind;
  el.textContent = text;
  $("#toasts").append(el);
  setTimeout(() => el.remove(), ms);
}

// ---------------------------------------------------------------- confirm dialog
/** Promise<boolean>. `danger` styles the confirm button; `requireCheck` adds a checkbox that must be ticked. */
export function confirmDialog({ title, body, confirmText = "Confirm", kind = "", requireCheck = null }) {
  const dlg = $("#dialog"), ok = $("#dialog-ok");
  $("#dialog-title").textContent = title;
  $("#dialog-body").innerHTML = body + (requireCheck
    ? `<label class="check" style="margin-top:12px"><input type="checkbox" id="dialog-check"> ${esc(requireCheck)}</label>` : "");
  ok.textContent = confirmText;
  ok.className = "btn" + (kind ? ` btn--${kind}` : "");
  const box = $("#dialog-check");
  ok.disabled = !!box;
  if (box) box.addEventListener("change", () => { ok.disabled = !box.checked; });
  return new Promise((resolve) => {
    dlg.returnValue = "";
    dlg.addEventListener("close", () => resolve(dlg.returnValue === "ok"), { once: true });
    dlg.showModal();
    $("#dialog-cancel").focus();
  });
}

export function promptDialog({ title, body = "", label, value = "", confirmText = "Save" }) {
  const dlg = $("#dialog");
  $("#dialog-title").textContent = title;
  $("#dialog-body").innerHTML = `${body}<label class="field" style="margin-top:10px">${esc(label)}<input id="dialog-input" value="${esc(value)}" maxlength="60" required></label>`;
  const ok = $("#dialog-ok");
  ok.textContent = confirmText; ok.className = "btn"; ok.disabled = false;
  return new Promise((resolve) => {
    dlg.returnValue = "";
    dlg.addEventListener("close", () => resolve(dlg.returnValue === "ok" ? $("#dialog-input").value.trim() : null), { once: true });
    dlg.showModal();
    $("#dialog-input").focus();
  });
}

export function unavailable(text) {
  return `<span class="na">${ICON("minus")}${esc(text)}</span>`;
}
export function infoTip(text) {
  return `<span class="info-tip" title="${esc(text)}" tabindex="0" role="img" aria-label="${esc(text)}">${ICON("info")}</span>`;
}

// ---------------------------------------------------------------- page meta row (farm · date · live time)
export const farm = { name: "Farm" };
export function metaRow(extra = "") {
  const d = new Date().toLocaleDateString([], { weekday: "short", day: "numeric", month: "long", year: "numeric" });
  return `<div class="meta"><span>${ICON("pin")}${esc(farm.name)}</span><span>${ICON("calendar")}${esc(d)}</span>
    <span>${ICON("clock")}<span data-clock>${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span></span>${extra}</div>`;
}
setInterval(() => document.querySelectorAll("[data-clock]").forEach((el) => {
  el.textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}), 10000);
