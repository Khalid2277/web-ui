// The window backdrop: real aerial imagery of this farm (Esri World Imagery tiles around the
// first field, or the drone's home point), graded and blurred under the glass. If the device is
// offline the painted gradient in CSS stays. A photo at /static/farm/backdrop.jpg takes priority.
import { api, live } from "./api.js";

const ZOOM = 17, TILE = 256;
const tileUrl = (z, x, y) => `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/${z}/${y}/${x}`;

function lonLatToTile(lat, lon, z) {
  const n = 2 ** z, r = (lat * Math.PI) / 180;
  return { x: ((lon + 180) / 360) * n, y: ((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2) * n };
}

let centre = null, built = "";
function build() {
  const scene = document.getElementById("scene");
  if (!scene || !centre) return;
  const w = window.innerWidth, h = window.innerHeight;
  const key = `${centre[0].toFixed(4)},${centre[1].toFixed(4)},${Math.ceil(w / TILE)},${Math.ceil(h / TILE)}`;
  if (key === built) return;
  built = key;
  const t = lonLatToTile(centre[0], centre[1], ZOOM);
  const cols = Math.ceil(w / TILE) + 2, rows = Math.ceil(h / TILE) + 2;
  const x0 = Math.floor(t.x - cols / 2), y0 = Math.floor(t.y - rows / 2);
  const grid = document.createElement("div");
  grid.className = "scene__tiles";
  grid.style.gridTemplateColumns = `repeat(${cols}, ${TILE}px)`;
  // place the centre point in the middle of the window
  grid.style.left = `${w / 2 - (t.x - x0) * TILE}px`;
  grid.style.top = `${h / 2 - (t.y - y0) * TILE}px`;
  let pending = cols * rows, failed = 0;
  const done = (ok) => {
    if (!ok) failed++;
    if (--pending === 0 && failed < (cols * rows) / 2) grid.dataset.ready = "true";
  };
  for (let y = y0; y < y0 + rows; y++) {
    for (let x = x0; x < x0 + cols; x++) {
      const img = new Image(TILE, TILE);
      img.alt = "";
      img.decoding = "async";
      img.onload = () => done(true);
      img.onerror = () => done(false);
      img.src = tileUrl(ZOOM, x, y);
      grid.append(img);
    }
  }
  scene.querySelectorAll(".scene__tiles").forEach((el) => el.remove());
  scene.append(grid);
}

export async function initScene() {
  const scene = document.getElementById("scene");
  // A custom photo (e.g. of the actual field) wins over the aerial tiles.
  try {
    const r = await fetch("/static/farm/backdrop.jpg", { method: "HEAD" });
    if (r.ok) {
      const img = new Image();
      img.className = "scene__photo";
      img.alt = "";
      img.src = "/static/farm/backdrop.jpg";
      scene.append(img);
      return;
    }
  } catch { /* offline: keep the gradient */ }
  try {
    const fields = await api("/api/fields");
    if (fields.length) {
      const p = fields[0].polygon;
      centre = [p.reduce((a, q) => a + q[0], 0) / p.length, p.reduce((a, q) => a + q[1], 0) / p.length];
    }
  } catch { /* fall through to home */ }
  if (!centre) {
    const unsub = live.subscribe((s) => {
      const h = s.telemetry?.home;
      if (h && !centre) { centre = [h.lat, h.lon]; build(); unsub(); }
    });
    setTimeout(() => { if (!centre) { centre = [25.3094, 55.4861]; build(); } }, 6000);
  } else build();
  let t;
  window.addEventListener("resize", () => { clearTimeout(t); t = setTimeout(build, 300); });
}
