// App shell: hash router, theme toggle, live-connection indicator, SIMULATOR / VIEW-ONLY / stale banners.
import { api, connect, live } from "./api.js";
import { switchTo } from "./conn-ui.js";
import { initScene } from "./scene.js";
import { $, $$, ICON, esc, farm } from "./util.js";

const ROUTES = { "": "overview", live: "live", map: "map", log: "log", analytics: "analytics", missions: "missions", system: "system" };
const TITLES = { overview: "Overview", live: "Operations", map: "Farm map", log: "Disease log", analytics: "Analytics",
  missions: "Missions", system: "System" };
let cleanup = null, routeToken = 0;

function parseHash() {
  const h = location.hash.replace(/^#\/?/, "");
  const [path, query = ""] = h.split("?");
  const parts = path.split("/").filter(Boolean);
  return { page: ROUTES[parts[0] || ""] || null, arg: parts[1] ? decodeURIComponent(parts[1]) : null,
    params: Object.fromEntries(new URLSearchParams(query)) };
}

async function route() {
  const { page, arg, params } = parseHash();
  const token = ++routeToken;
  if (cleanup) { try { cleanup(); } catch (e) { console.error(e); } cleanup = null; }
  const view = $("#view");
  closeNav();
  if (!page) {
    view.innerHTML = `<div class="empty">${ICON("map", "icon icon--lg")}<strong>Page not found</strong><a href="#/">Go to the overview</a></div>`;
    return;
  }
  $$(".nav a").forEach((a) => {
    if (a.dataset.route === page) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  document.title = `${TITLES[page]} · Farm Command Center`;
  view.innerHTML = `<div class="skeleton" style="height:40px;width:40%"></div><div class="skeleton" style="height:220px;margin-top:16px"></div>`;
  try {
    const mod = await import(`./pages/${page}.js`);
    if (token !== routeToken) return;
    view.innerHTML = "";
    view.classList.remove("reveal");
    cleanup = (await mod.mount(view, { arg, params })) || null;
    void view.offsetWidth;          // restart the entrance for each page
    view.classList.add("reveal");
  } catch (e) {
    console.error(e);
    if (token !== routeToken) return;
    view.innerHTML = `<div class="empty">${ICON("alert", "icon icon--lg")}<strong>This page could not load</strong><p>${esc(e.message)}</p>
      <button class="btn btn--ghost" onclick="location.reload()">Reload</button></div>`;
  }
  view.focus({ preventScroll: true });
  window.scrollTo(0, 0);
}

// ---------------------------------------------------------------- navigation drawer (tablet/phone)
function closeNav() { $("#nav").dataset.open = "false"; $("#nav-toggle").setAttribute("aria-expanded", "false"); }
$("#nav-toggle").addEventListener("click", () => {
  const open = $("#nav").dataset.open !== "true";
  $("#nav").dataset.open = String(open);
  $("#nav-toggle").setAttribute("aria-expanded", String(open));
});

// ---------------------------------------------------------------- theme
function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  const btn = $("#theme-btn");
  btn.innerHTML = ICON(t === "dark" ? "sun" : "moon", "icon");
  btn.setAttribute("aria-label", `Switch to ${t === "dark" ? "light" : "dark"} theme`);
  try { localStorage.setItem("farm-theme", t); } catch { /* private mode */ }
  window.dispatchEvent(new CustomEvent("themechange", { detail: t }));
}
$("#theme-btn").addEventListener("click", () => applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));
applyTheme(document.documentElement.dataset.theme === "light" ? "light" : "dark");

// ---------------------------------------------------------------- connection + banners
const CONN_TEXT = { connecting: "Connecting", live: "Live", stale: "Delayed", reconnecting: "Reconnecting", offline: "Offline" };
live.onStatus((s) => {
  const el = $("#conn");
  el.dataset.state = s;
  el.querySelector("span").textContent = CONN_TEXT[s] || s;
  const banner = $("#stale-banner");
  banner.hidden = s === "live" || s === "connecting";
  $("#stale-text").textContent = s === "stale"
    ? "Live data has not updated for a few seconds. Values shown may be out of date."
    : "Lost connection to the console. Reconnecting automatically; values shown are out of date.";
  document.body.dataset.stale = String(s !== "live");
});
let chipMode = null;
live.subscribe((s) => {
  const mode = s.mode || (s.sim ? "sim" : "real"), chip = $("#mode-chip");
  if (mode !== chipMode) {
    if (chipMode !== null) route();     // records differ per mode: reload the page's data
    chipMode = mode;
    chip.hidden = false;
    chip.dataset.mode = mode;
    chip.innerHTML = `${ICON(mode === "sim" ? "cpu" : "drone")}<span>${mode === "sim" ? "Simulator" : "Real drone"}</span>`;
    chip.setAttribute("aria-label", `Connected to the ${mode === "sim" ? "simulator" : "real drone"}. Switch to the ${mode === "sim" ? "real drone" : "simulator"}.`);
  }
  $("#sim-banner").hidden = !s.sim;
  $("#view-banner").hidden = s.local !== false;
  document.body.dataset.local = String(s.local !== false);
});

const settingsReady = api("/api/settings").then((s) => { farm.name = s.farm_name; $("#farm-name").textContent = s.farm_name; }).catch(() => {});
window.addEventListener("farmname", (e) => { farm.name = e.detail; $("#farm-name").textContent = e.detail; });

window.addEventListener("hashchange", route);
$("#mode-chip").addEventListener("click", () => switchTo(chipMode === "sim" ? "real" : "sim"));
connect();
settingsReady.finally(route);
initScene();
