// Small dependency-free SVG charts: line, stacked bars, horizontal bars, donut, gauge.
// Each one has hover tooltips, a legend for >= 2 series, and a "table view" fallback.
import { esc, isNum } from "./util.js";

const NS = "http://www.w3.org/2000/svg";
let gid = 0;

function tooltip(host) {
  let tip = host.querySelector(".tooltip");
  if (!tip) { tip = document.createElement("div"); tip.className = "tooltip"; tip.hidden = true; host.append(tip); }
  return {
    show(htmlText, x, y) {
      tip.innerHTML = htmlText; tip.hidden = false;
      const w = tip.offsetWidth, hw = host.clientWidth;
      tip.style.left = `${Math.min(Math.max(0, x + 12), hw - w)}px`;
      tip.style.top = `${Math.max(0, y - tip.offsetHeight - 8)}px`;
    },
    hide() { tip.hidden = true; },
  };
}

function legend(series) {
  if (series.length < 2) return "";
  return `<ul class="legend">${series.map((s) => `<li><span class="sw" style="background:${s.color}"></span>${esc(s.name)}</li>`).join("")}</ul>`;
}

function tableView(head, rows, caption) {
  return `<details class="table-view"><summary>Show as table</summary><div class="table-wrap"><table><caption class="sr">${esc(caption)}</caption>
    <thead><tr>${head.map((h) => `<th scope="col">${esc(h)}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${esc(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div></details>`;
}

const niceMax = (v) => {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v)), n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
};

/** Time series. series: [{name, color, points: [[t, value|null], ...]}] */
export function lineChart(host, { series, yMax, yLabel = "", format = (v) => v.toFixed(0), xFormat, caption = "Line chart", height = 220 }) {
  const W = Math.max(280, host.clientWidth || 600), H = height, L = 40, R = 12, T = 12, B = 26;
  const all = series.flatMap((s) => s.points).filter((p) => isNum(p[1]));
  if (!all.length) { host.innerHTML = ""; return false; }
  const ts = all.map((p) => p[0]);
  let t0 = Math.min(...ts), t1 = Math.max(...ts);
  if (t1 === t0) { t0 -= 43200; t1 += 43200; }
  const top = yMax ?? niceMax(Math.max(...all.map((p) => p[1])));
  const x = (t) => L + ((t - t0) / (t1 - t0)) * (W - L - R);
  const y = (v) => T + (1 - v / top) * (H - T - B);
  const xf = xFormat || ((t) => new Date(t * 1000).toLocaleDateString([], { day: "numeric", month: "short" }));
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(caption)}">`;
  for (let i = 0; i <= 4; i++) {
    const v = (top * i) / 4;
    svg += `<line class="grid-line" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text class="axis-text" x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${esc(format(v))}</text>`;
  }
  for (let i = 0; i <= 3; i++) {
    const t = t0 + ((t1 - t0) * i) / 3;
    svg += `<text class="axis-text" x="${x(t)}" y="${H - 6}" text-anchor="${i === 0 ? "start" : i === 3 ? "end" : "middle"}">${esc(xf(t))}</text>`;
  }
  for (const s of series) {
    let d = "", pen = false;
    for (const [t, v] of s.points) {
      if (!isNum(v)) { pen = false; continue; }
      d += `${pen ? "L" : "M"}${x(t).toFixed(1)},${y(v).toFixed(1)}`; pen = true;
    }
    const pts = s.points.filter((p) => isNum(p[1]));
    if (series.length === 1 && pts.length > 1) {
      const gidA = `la${++gid}`;
      svg += `<defs><linearGradient id="${gidA}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${s.color}" stop-opacity=".35"/><stop offset="1" stop-color="${s.color}" stop-opacity="0"/></linearGradient></defs>
        <path d="M${x(pts[0][0])},${y(0)} ${pts.map((p) => `L${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join(" ")} L${x(pts.at(-1)[0])},${y(0)}Z" fill="url(#${gidA})"/>`;
    }
    svg += `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round"/>`;
    for (const [t, v] of s.points) if (isNum(v)) svg += `<circle cx="${x(t)}" cy="${y(v)}" r="4.5" fill="${s.color}" stroke="rgba(255,255,255,.85)" stroke-width="2"/>`;
  }
  svg += `<line class="cross" x1="0" x2="0" y1="${T}" y2="${H - B}" visibility="hidden"/><rect class="hit" x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}"/></svg>`;
  const rows = [...new Set(all.map((p) => p[0]))].sort((a, b) => a - b)
    .map((t) => [xf(t), ...series.map((s) => { const p = s.points.find((q) => q[0] === t); return p && isNum(p[1]) ? format(p[1]) : "—"; })]);
  host.innerHTML = `<div class="chart">${svg}</div>${legend(series)}${tableView(["Time", ...series.map((s) => s.name)], rows, caption)}`;
  const chart = host.querySelector(".chart"), tip = tooltip(chart), cross = chart.querySelector(".cross"), svgEl = chart.querySelector("svg");
  const times = [...new Set(all.map((p) => p[0]))].sort((a, b) => a - b);
  chart.querySelector(".hit").addEventListener("pointermove", (ev) => {
    const r = svgEl.getBoundingClientRect(), k = W / r.width, px = (ev.clientX - r.left) * k;
    const t = times.reduce((a, b) => (Math.abs(x(b) - px) < Math.abs(x(a) - px) ? b : a));
    cross.setAttribute("x1", x(t)); cross.setAttribute("x2", x(t)); cross.setAttribute("visibility", "visible");
    const lines = series.map((s) => { const p = s.points.find((q) => q[0] === t); return p && isNum(p[1]) ? `<div><span class="sw" style="background:${s.color}"></span>${esc(s.name)}: <b style="display:inline">${esc(format(p[1]))}${esc(yLabel)}</b></div>` : ""; }).join("");
    tip.show(`<b>${esc(xf(t))}</b>${lines}`, x(t) / k, (ev.clientY - r.top));
  });
  chart.querySelector(".hit").addEventListener("pointerleave", () => { tip.hide(); cross.setAttribute("visibility", "hidden"); });
  return true;
}

/** Stacked vertical bars. categories: [label], series: [{name, color, values:[...]}] */
export function barChart(host, { categories, series, caption = "Bar chart", height = 220, format = (v) => String(v) }) {
  const W = Math.max(280, host.clientWidth || 600), H = height, L = 34, R = 8, T = 10, B = 26;
  const totals = categories.map((_, i) => series.reduce((a, s) => a + (s.values[i] || 0), 0));
  const top = niceMax(Math.max(1, ...totals));
  const bw = (W - L - R) / categories.length, gap = Math.min(6, bw * 0.25);
  const y = (v) => T + (1 - v / top) * (H - T - B);
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(caption)}">`;
  for (let i = 0; i <= 4; i++) {
    const v = (top * i) / 4;
    svg += `<line class="grid-line" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text class="axis-text" x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${+v.toFixed(2)}</text>`;
  }
  const step = Math.ceil(categories.length / 8);
  categories.forEach((c, i) => {
    let acc = 0;
    const x0 = L + i * bw + gap / 2, w = Math.max(1, bw - gap);
    series.forEach((s) => {
      const v = s.values[i] || 0;
      if (!v) return;
      const y1 = y(acc), y2 = y(acc + v);
      svg += `<rect x="${x0}" y="${y2 + 1}" width="${w}" height="${Math.max(0, y1 - y2 - 2)}" rx="${Math.min(3, w / 3)}" fill="${s.color}"/>`;
      acc += v;
    });
    if (i % step === 0) svg += `<text class="axis-text" x="${x0 + w / 2}" y="${H - 6}" text-anchor="middle">${esc(c)}</text>`;
    svg += `<rect class="hit" data-i="${i}" x="${L + i * bw}" y="${T}" width="${bw}" height="${H - T - B}"/>`;
  });
  svg += "</svg>";
  const rows = categories.map((c, i) => [c, ...series.map((s) => s.values[i] || 0), totals[i]]);
  host.innerHTML = `<div class="chart">${svg}</div>${legend(series)}${tableView(["", ...series.map((s) => s.name), "Total"], rows, caption)}`;
  const chart = host.querySelector(".chart"), tip = tooltip(chart), svgEl = chart.querySelector("svg");
  chart.querySelectorAll(".hit").forEach((el) => {
    el.addEventListener("pointermove", (ev) => {
      const i = +el.dataset.i, r = svgEl.getBoundingClientRect();
      const lines = series.filter((s) => s.values[i]).map((s) => `<div><span class="sw" style="background:${s.color}"></span>${esc(s.name)}: ${esc(format(s.values[i]))}</div>`).join("");
      tip.show(`<b>${esc(categories[i])} · ${esc(format(totals[i]))}</b>${lines || "<div>None</div>"}`, ev.clientX - r.left, ev.clientY - r.top);
    });
    el.addEventListener("pointerleave", () => tip.hide());
  });
}

/** Horizontal bars with direct value labels. items: [{name, value, color}] */
export function hbarChart(host, { items, caption = "Bar chart", format = (v) => String(v) }) {
  const max = Math.max(1, ...items.map((i) => i.value));
  host.innerHTML = `<div class="chart" role="img" aria-label="${esc(caption)}">${items.map((i) => `
    <div style="display:grid;grid-template-columns:minmax(90px,38%) 1fr auto;gap:10px;align-items:center;margin:6px 0;font-size:.84rem">
      <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(i.name)}">${esc(i.name)}</span>
      <span style="height:12px;background:var(--glass-chip-2);border-radius:4px;overflow:hidden"><i style="display:block;height:100%;width:${(100 * i.value) / max}%;background:${i.color};border-radius:4px"></i></span>
      <span class="mono">${esc(format(i.value))}</span></div>`).join("")}</div>
    ${tableView(["Item", "Value"], items.map((i) => [i.name, format(i.value)]), caption)}`;
}

/** Donut with centre label. items: [{name, value, color}] */
export function donutChart(host, { items, center = "", sub = "", caption = "Donut chart", format = (v) => String(v) }) {
  const total = items.reduce((a, i) => a + i.value, 0);
  const S = 180, r = 70, w = 22, C = 2 * Math.PI * r;
  let off = 0, arcs = "";
  items.forEach((it, k) => {
    if (!it.value) return;
    const len = (it.value / total) * C;
    arcs += `<circle data-k="${k}" cx="${S / 2}" cy="${S / 2}" r="${r}" fill="none" stroke="${it.color}" stroke-width="${w}"
      stroke-dasharray="${Math.max(0, len - 2)} ${C}" stroke-dashoffset="${-off}" transform="rotate(-90 ${S / 2} ${S / 2})" style="cursor:pointer"/>`;
    off += len;
  });
  host.innerHTML = `<div class="gauge-wrap"><div class="chart" style="width:${S}px;flex:none"><svg viewBox="0 0 ${S} ${S}" role="img" aria-label="${esc(caption)}">
      <circle cx="${S / 2}" cy="${S / 2}" r="${r}" fill="none" stroke="var(--glass-chip-2)" stroke-width="${w}"/>${arcs}
      <text x="50%" y="48%" text-anchor="middle" style="font:600 24px Outfit,sans-serif;fill:var(--text)">${esc(center)}</text>
      <text x="50%" y="62%" text-anchor="middle" class="axis-text">${esc(sub)}</text></svg></div>
    <ul class="legend" style="flex-direction:column;gap:6px;margin:0">${items.map((i) => `<li><span class="sw" style="background:${i.color}"></span>${esc(i.name)} <span class="mono muted">${esc(format(i.value))} · ${total ? Math.round((100 * i.value) / total) : 0}%</span></li>`).join("")}</ul></div>
    ${tableView(["Item", "Count", "Share"], items.map((i) => [i.name, format(i.value), `${total ? ((100 * i.value) / total).toFixed(1) : 0}%`]), caption)}`;
  const chart = host.querySelector(".chart"), tip = tooltip(chart);
  chart.querySelectorAll("circle[data-k]").forEach((el) => {
    el.addEventListener("pointermove", (ev) => {
      const it = items[+el.dataset.k], r0 = chart.getBoundingClientRect();
      tip.show(`<b>${esc(it.name)}</b>${esc(format(it.value))} · ${Math.round((100 * it.value) / total)}%`, ev.clientX - r0.left, ev.clientY - r0.top);
    });
    el.addEventListener("pointerleave", () => tip.hide());
  });
}

/** Gradient arc gauge (270°) with a light, large numeral — or an honest "not available". */
export function gauge(value, { label = "", size = 190, caption = "", sub = "" } = {}) {
  const id = `g${++gid}`, v = isNum(value) ? Math.max(0, Math.min(100, value)) : null;
  const r = 70, cx = 90, cy = 90, sweep = 270, C = 2 * Math.PI * r, arc = (sweep / 360) * C;
  const word = v === null ? "No data" : v >= 85 ? "Excellent" : v >= 70 ? "Good" : v >= 50 ? "Fair" : "Poor";
  const ticks = Array.from({ length: 28 }, (_, i) => {
    const a = ((135 + (i / 27) * sweep) * Math.PI) / 180, on = v !== null && i / 27 <= v / 100;
    return `<line x1="${cx + 84 * Math.cos(a)}" y1="${cy + 84 * Math.sin(a)}" x2="${cx + 88 * Math.cos(a)}" y2="${cy + 88 * Math.sin(a)}"
      stroke="${on ? "var(--text-2)" : "var(--line-strong)"}" stroke-width="1.5" stroke-linecap="round"/>`;
  }).join("");
  return `<div class="gauge"><svg viewBox="0 0 180 170" width="${size}" role="img" aria-label="${esc(label)}: ${v === null ? "not available" : v.toFixed(0) + "%"}">
    <defs><linearGradient id="${id}" x1="0" y1="1" x2="1" y2="0"><stop offset="0" stop-color="var(--gauge-a)"/><stop offset=".55" stop-color="var(--gauge-b)"/><stop offset="1" stop-color="var(--gauge-c)"/></linearGradient></defs>
    ${ticks}
    <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="var(--glass-chip-2)" stroke-width="12" stroke-linecap="round"
      stroke-dasharray="${arc} ${C}" transform="rotate(135 ${cx} ${cy})"/>
    ${v === null ? "" : `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="url(#${id})" stroke-width="12" stroke-linecap="round"
      stroke-dasharray="${(v / 100) * arc} ${C}" transform="rotate(135 ${cx} ${cy})"/>`}
    <text x="${cx}" y="${cy + 8}" text-anchor="middle" style="font:200 44px Outfit,sans-serif;fill:var(--text);letter-spacing:-2px">${v === null ? "—" : v.toFixed(0)}<tspan style="font-size:18px;font-weight:400" dx="2">${v === null ? "" : "%"}</tspan></text>
    <text x="${cx}" y="${cy + 30}" text-anchor="middle" style="font:600 12px Manrope,sans-serif;fill:var(--text-2)">${esc(sub || word)}</text>
  </svg>${caption ? `<span class="gauge__label">${esc(caption)}</span>` : ""}</div>`;
}

/** Thick segmented ring with gaps (identity colours) and a centre total. items: [{name, value, color}] */
export function ringChart(host, { items, center = "", sub = "", caption = "Ring chart", format = (v) => String(v), size = 210 }) {
  const total = items.reduce((a, i) => a + i.value, 0);
  const S = 200, r = 74, w = 18, C = 2 * Math.PI * r, gap = items.filter((i) => i.value).length > 1 ? 6 : 0;
  let off = 0, arcs = "";
  items.forEach((it, k) => {
    if (!it.value) return;
    const len = (it.value / total) * C;
    arcs += `<circle data-k="${k}" cx="${S / 2}" cy="${S / 2}" r="${r}" fill="none" stroke="${it.color}" stroke-width="${w}" stroke-linecap="round"
      stroke-dasharray="${Math.max(0.1, len - gap - w * 0.9)} ${C}" stroke-dashoffset="${-off - w * 0.45}" transform="rotate(-90 ${S / 2} ${S / 2})" style="cursor:pointer"/>`;
    off += len;
  });
  host.innerHTML = `<div class="gauge-wrap" style="justify-content:center"><div class="chart" style="width:${size}px;flex:none">
      <svg viewBox="0 0 ${S} ${S}" role="img" aria-label="${esc(caption)}">
      <circle cx="${S / 2}" cy="${S / 2}" r="${r + 18}" fill="none" stroke="var(--line)" stroke-dasharray="1 5"/>
      <circle cx="${S / 2}" cy="${S / 2}" r="${r}" fill="none" stroke="var(--glass-chip)" stroke-width="${w}"/>${arcs}
      <text x="50%" y="49%" text-anchor="middle" style="font:200 40px Outfit,sans-serif;fill:var(--text);letter-spacing:-1.5px">${esc(center)}</text>
      <text x="50%" y="61%" text-anchor="middle" style="font:600 11px Manrope,sans-serif;fill:var(--text-3)">${esc(sub)}</text></svg></div>
    <ul class="legend" style="flex-direction:column;gap:7px;margin:0">${items.map((i) => `<li><span class="sw" style="background:${i.color}"></span>${esc(i.name)} <span class="muted">${esc(format(i.value))} · ${total ? Math.round((100 * i.value) / total) : 0}%</span></li>`).join("")}</ul></div>
    ${tableView(["Item", "Count", "Share"], items.map((i) => [i.name, format(i.value), `${total ? ((100 * i.value) / total).toFixed(1) : 0}%`]), caption)}`;
  const chart = host.querySelector(".chart"), tip = tooltip(chart);
  chart.querySelectorAll("circle[data-k]").forEach((el) => {
    el.addEventListener("pointermove", (ev) => {
      const it = items[+el.dataset.k], r0 = chart.getBoundingClientRect();
      tip.show(`<b>${esc(it.name)}</b>${esc(format(it.value))} · ${Math.round((100 * it.value) / total)}%`, ev.clientX - r0.left, ev.clientY - r0.top);
    });
    el.addEventListener("pointerleave", () => tip.hide());
  });
}

/** Radial burst: one ray per bucket (e.g. hour of day), length = value. */
export function burstChart(host, { values, labels, caption = "Radial chart", center = "", sub = "", format = (v) => String(v), size = 220 }) {
  const S = 250, c = S / 2, r0 = 34, r1 = 98, max = Math.max(1, ...values), n = values.length;
  const id = `b${++gid}`;
  const rays = values.map((v, i) => {
    const a = (-90 + (360 * i) / n) * Math.PI / 180, len = r0 + 4 + (v / max) * (r1 - r0 - 4);
    return `<line data-i="${i}" x1="${c + r0 * Math.cos(a)}" y1="${c + r0 * Math.sin(a)}" x2="${c + len * Math.cos(a)}" y2="${c + len * Math.sin(a)}"
      stroke="${v ? `url(#${id})` : "var(--line-strong)"}" stroke-width="${v ? 3.2 : 1.5}" stroke-linecap="round"/>`;
  }).join("");
  host.innerHTML = `<div class="chart" style="max-width:${size}px;margin:0 auto"><svg viewBox="0 0 ${S} ${S}" role="img" aria-label="${esc(caption)}">
      <defs><radialGradient id="${id}" cx="${c}" cy="${c}" r="${r1}" gradientUnits="userSpaceOnUse"><stop offset=".3" stop-color="var(--gauge-c)"/><stop offset="1" stop-color="var(--gauge-a)"/></radialGradient></defs>
      <circle cx="${c}" cy="${c}" r="${r1 + 6}" fill="none" stroke="var(--line)" stroke-dasharray="1 5"/>${rays}
      ${[0, n / 4, n / 2, (3 * n) / 4].map((i) => { const a = (-90 + (360 * i) / n) * Math.PI / 180, rr = r1 + 14;
        return `<text x="${c + rr * Math.cos(a)}" y="${c + rr * Math.sin(a) + 3.5}" text-anchor="middle" class="axis-text">${esc(labels[Math.round(i)] ?? "")}</text>`; }).join("")}
      <text x="50%" y="50%" text-anchor="middle" style="font:300 22px Outfit,sans-serif;fill:var(--text)">${esc(center)}</text>
      <text x="50%" y="58%" text-anchor="middle" style="font:600 9.5px Manrope,sans-serif;fill:var(--text-3)">${esc(sub)}</text></svg></div>
    ${tableView(["Bucket", "Value"], values.map((v, i) => [labels[i], format(v)]), caption)}`;
  const chart = host.querySelector(".chart"), tip = tooltip(chart);
  chart.querySelectorAll("line[data-i]").forEach((el) => {
    el.style.cursor = "pointer";
    el.addEventListener("pointermove", (ev) => { const i = +el.dataset.i, r = chart.getBoundingClientRect(); tip.show(`<b>${esc(labels[i])}</b>${esc(format(values[i]))}`, ev.clientX - r.left, ev.clientY - r.top); });
    el.addEventListener("pointerleave", () => tip.hide());
  });
}

/** Mirrored waveform bars (one per bucket) with hover; used for counts over days. */
export function waveChart(host, { values, labels, caption = "Bar chart", format = (v) => String(v), height = 96 }) {
  const max = Math.max(1, ...values);
  host.innerHTML = `<div class="chart"><div class="wave" style="height:${height}px" role="img" aria-label="${esc(caption)}">${values.map((v, i) =>
    `<i data-i="${i}" data-zero="${!v}" style="height:${v ? 14 + (86 * v) / max : 6}%"></i>`).join("")}</div>
    <div class="row small muted" style="justify-content:space-between;margin-top:6px"><span>${esc(labels[0])}</span><span>${esc(labels.at(-1))}</span></div></div>
    ${tableView(["Bucket", "Value"], values.map((v, i) => [labels[i], format(v)]), caption)}`;
  const chart = host.querySelector(".chart"), tip = tooltip(chart);
  chart.querySelectorAll(".wave i").forEach((el) => {
    el.addEventListener("pointermove", (ev) => { const i = +el.dataset.i, r = chart.getBoundingClientRect(); tip.show(`<b>${esc(labels[i])}</b>${esc(format(values[i]))}`, ev.clientX - r.left, ev.clientY - r.top); });
    el.addEventListener("pointerleave", () => tip.hide());
  });
}

/** Strip of rounded cells coloured on one sequential hue (more = stronger). items: [{label, value}] */
export function cellStrip(host, { items, color = "var(--k-disease)", columns = 24, caption = "Cells", format = (v) => String(v) }) {
  const max = Math.max(1, ...items.map((i) => i.value));
  host.innerHTML = `<div class="chart"><div class="cells" style="grid-template-columns:repeat(${Math.min(columns, items.length)},minmax(0,1fr))" role="img" aria-label="${esc(caption)}">
    ${items.map((it, i) => `<i data-i="${i}" style="${it.value ? `background:color-mix(in srgb, ${color} ${25 + (75 * it.value) / max}%, transparent)` : ""}"></i>`).join("")}</div></div>
    ${tableView(["Cell", "Value"], items.map((i) => [i.label, format(i.value)]), caption)}`;
  const chart = host.querySelector(".chart"), tip = tooltip(chart);
  chart.querySelectorAll(".cells i").forEach((el) => {
    el.addEventListener("pointermove", (ev) => { const it = items[+el.dataset.i], r = chart.getBoundingClientRect(); tip.show(`<b>${esc(it.label)}</b>${esc(format(it.value))}`, ev.clientX - r.left, ev.clientY - r.top); });
    el.addEventListener("pointerleave", () => tip.hide());
  });
}

/** Tiny inline sparkline (no axes) for live telemetry. */
export function sparkline(values, { w = 120, h = 30, color = "var(--accent)" } = {}) {
  const v = values.filter(isNum);
  if (v.length < 2) return `<svg width="${w}" height="${h}" aria-hidden="true"></svg>`;
  let lo = Math.min(...v), hi = Math.max(...v);
  if (hi - lo < 0.2) { const m = (hi + lo) / 2; lo = m - 0.1; hi = m + 0.1; }
  const pts = values.map((x, i) => (isNum(x) ? `${(i / (values.length - 1)) * w},${h - 2 - ((x - lo) / (hi - lo)) * (h - 4)}` : null)).filter(Boolean);
  return `<svg width="${w}" height="${h}" aria-hidden="true"><polyline points="${pts.join(" ")}" fill="none" stroke="${color}" stroke-width="1.6" stroke-linejoin="round"/></svg>`;
}
