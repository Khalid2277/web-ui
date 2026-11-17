// Disease monitoring log: searchable, filterable, sortable table; detail drawer; review; CSV export.
import { api, live } from "../api.js";
import { metaRow, ICON, KIND_TEXT, dateTime, esc, fmt, kindOf, kindTag, labelName, latlon, pct, toast } from "../util.js";

const PAGE = 50;

export async function mount(root, { params }) {
  root.innerHTML = `
    <div class="page-head"><div><h1>Disease log</h1>${metaRow()}
      <p>Every analysed capture, from inspection flights and manual snapshots. Click a row for the photo, position and guidance.</p></div>
      <a class="btn btn--ghost" id="csv" href="/api/detections.csv" download>${ICON("download")}Export CSV</a></div>
    <section class="card">
      <form class="filters" id="filters" role="search" onsubmit="return false">
        <label class="field search">Search<input id="q" type="search" placeholder="Condition, mission or note" autocomplete="off"></label>
        <label class="field">Type<select id="kind"><option value="">All</option>${["disease", "deficiency", "pest", "healthy", "none"].map((k) => `<option value="${k}">${KIND_TEXT[k]}</option>`).join("")}</select></label>
        <label class="field">Condition<select id="label"><option value="">Any</option></select></label>
        <label class="field">Field<select id="field"><option value="">All fields</option></select></label>
        <label class="field">Mission<select id="mission"><option value="">All</option></select></label>
        <label class="field">From<input id="from" type="date"></label>
        <label class="field">To<input id="to" type="date"></label>
        <label class="field">Review<select id="review"><option value="">Any</option><option value="new">New</option><option value="confirmed">Confirmed</option><option value="dismissed">Dismissed</option></select></label>
        <button class="btn btn--ghost btn--sm" type="button" id="reset">Clear</button>
      </form>
      <div class="table-wrap"><table aria-describedby="count">
        <thead><tr><th scope="col"><span class="sr">Photo</span></th>
          <th scope="col"><button data-sort="label">Condition</button></th><th scope="col"><button data-sort="p">Confidence</button></th>
          <th scope="col"><button data-sort="t">Time</button></th><th scope="col" class="hide-sm">Field</th><th scope="col" class="hide-sm">Position</th>
          <th scope="col" class="hide-sm">Source</th><th scope="col">Review</th></tr></thead>
        <tbody id="rows"></tbody></table></div>
      <div class="pager"><span id="count" role="status"></span><button class="btn btn--ghost btn--sm" id="prev">Previous</button><button class="btn btn--ghost btn--sm" id="next">Next</button></div>
    </section>
    <aside class="drawer" id="drawer" data-open="false" aria-label="Detection details" tabindex="-1"></aside>`;
  const $ = (s) => root.querySelector(s);
  let sort = "t", order = "desc", offset = 0, total = 0, fieldNames = {}, rows = [];

  const [fields, missions, all] = await Promise.all([api("/api/fields?archived=1").catch(() => []), api("/api/missions?limit=100").catch(() => []),
    api("/api/analytics?days=365").catch(() => null)]);
  fieldNames = Object.fromEntries(fields.map((f) => [f.id, f.name]));
  $("#field").innerHTML += fields.map((f) => `<option value="${esc(f.id)}">${esc(f.name)}</option>`).join("");
  $("#mission").innerHTML += missions.filter((m) => m.captures).map((m) => `<option value="${esc(m.id)}">${esc(m.id)}</option>`).join("");
  const labels = (all?.distribution_all?.by_label || []).map((r) => r.label);
  $("#label").innerHTML += [...new Set(labels)].sort().map((l) => `<option value="${esc(l)}">${esc(labelName(l))}</option>`).join("");
  for (const k of ["q", "kind", "label", "field", "mission", "review"]) if (params[k]) $(`#${k}`).value = params[k];

  function query() {
    const q = new URLSearchParams({ sort, order });
    for (const k of ["q", "kind", "label", "field", "mission", "review"]) if ($(`#${k}`).value) q.set(k, $(`#${k}`).value);
    if ($("#from").value) q.set("from", String(new Date($("#from").value + "T00:00").getTime() / 1000));
    if ($("#to").value) q.set("to", String(new Date($("#to").value + "T00:00").getTime() / 1000 + 86400));
    return q;
  }
  async function load() {
    const q = query();
    $("#csv").href = `/api/detections.csv?${q}`;
    q.set("limit", PAGE); q.set("offset", offset);
    $("#rows").innerHTML = `<tr><td colspan="8"><div class="skeleton"></div></td></tr>`;
    let res;
    try { res = await api(`/api/log?${q}`); } catch (e) { $("#rows").innerHTML = `<tr><td colspan="8">${esc(e.message)}</td></tr>`; return; }
    total = res.total; rows = res.items;
    $("#rows").innerHTML = rows.length ? rows.map((d) => `<tr data-id="${esc(d.id)}" tabindex="0">
        <td>${d.thumb ? `<img class="thumb" src="/captures/${encodeURI(d.thumb)}" alt="" loading="lazy">` : ""}</td>
        <td>${kindTag(d.label, d.status)}${d.status === "uncertain" ? ' <span class="tag">uncertain</span>' : ""}</td>
        <td class="mono">${pct(d.p)}</td><td class="mono small">${esc(dateTime(d.t))}</td>
        <td class="hide-sm">${esc(fieldNames[d.field_id] || "—")}</td>
        <td class="hide-sm mono small">${d.lat != null ? esc(latlon(d.lat, d.lon)) : '<span class="muted">no GPS</span>'}</td>
        <td class="hide-sm small">${d.source === "mission" ? "Flight" : "Snapshot"}</td>
        <td><span class="tag ${d.review_status === "confirmed" ? "tag--ok" : d.review_status === "dismissed" ? "" : "tag--info"}">${esc(d.review_status)}</span></td></tr>`).join("")
      : `<tr><td colspan="8"><div class="empty">${ICON("search", "icon icon--lg")}<strong>No detections match</strong><span>Change or clear the filters.</span></div></td></tr>`;
    $("#count").textContent = total ? `${offset + 1}–${Math.min(offset + PAGE, total)} of ${total}` : "0 results";
    $("#prev").disabled = offset === 0; $("#next").disabled = offset + PAGE >= total;
    root.querySelectorAll("th button").forEach((b) => {
      const th = b.closest("th");
      th.setAttribute("aria-sort", b.dataset.sort === sort ? (order === "asc" ? "ascending" : "descending") : "none");
      b.innerHTML = b.textContent.replace(/[▲▼]/g, "").trim() + (b.dataset.sort === sort ? (order === "asc" ? " ▲" : " ▼") : "");
    });
  }

  // ---------------------------------------------------------------- detail drawer
  const drawer = $("#drawer");
  async function open(id) {
    let d;
    try { d = await api(`/api/detections/${encodeURIComponent(id)}`); } catch (e) { toast(e.message, "error"); return; }
    root.querySelectorAll("tbody tr").forEach((tr) => tr.setAttribute("aria-selected", String(tr.dataset.id === id)));
    const k = kindOf(d.label, d.status), local = live.state?.local !== false;
    const leaves = (d.leaves || []).filter((l) => l.label !== "no_tomato_leaf");
    drawer.innerHTML = `<div class="row"><h2 style="flex:1">${esc(labelName(d.label))}</h2><button class="icon-btn" id="close" aria-label="Close details">${ICON("close", "icon")}</button></div>
      <p class="small muted" style="margin:4px 0 12px">${esc(KIND_TEXT[k])} · ${pct(d.p)} confidence${d.status === "uncertain" ? " (below the threshold, treat as unsure)" : ""}</p>
      ${d.image ? `<a href="/captures/${encodeURI(d.image)}" target="_blank" rel="noopener"><img class="full" src="/captures/${encodeURI(d.thumb || d.image)}" alt="Captured frame"></a>` : ""}
      <dl class="kv" style="margin-top:14px">
        <dt>Time</dt><dd>${esc(dateTime(d.t))}</dd>
        <dt>Position</dt><dd>${esc(latlon(d.lat, d.lon))}${d.h_acc_m != null ? ` ± ${fmt(d.h_acc_m, 1)} m` : ""}</dd>
        <dt>Altitude</dt><dd>${d.alt != null ? `${fmt(d.alt, 1)} m` : "—"}</dd>
        <dt>Field</dt><dd>${esc(d.field?.name || "—")}</dd>
        <dt>Mission</dt><dd>${d.mission_id ? `<a href="#/missions/${encodeURIComponent(d.mission_id)}">${esc(d.mission_id)}</a>` : "Manual snapshot"}</dd>
        <dt>Severity</dt><dd>Not assessed by the model</dd>
        <dt>Model</dt><dd>${esc(d.model || "—")}</dd>
        <dt>Leaves</dt><dd>${d.n_leaves} (${d.n_healthy} healthy · ${d.n_problem} problem · ${d.n_uncertain} uncertain)</dd>
        ${d.truth ? `<dt>Test label</dt><dd>${esc(labelName(d.truth))} ${d.truth === d.label ? "(match)" : "(mismatch)"}</dd>` : ""}
      </dl>
      ${d.lat != null ? `<a class="btn btn--ghost btn--sm" style="margin-top:12px" href="#/map?id=${encodeURIComponent(d.id)}">${ICON("pin")}Show on map</a>` : `<p class="small muted" style="margin-top:12px">No GPS fix (or stale telemetry) when captured, so it is not on the map.</p>`}
      ${d.guidance ? `<div class="guidance" style="margin-top:14px">${esc(d.guidance)}<small>General guidance — confirm with an agronomist before treating.</small></div>` : ""}
      ${(d.top || []).length ? `<h3 style="margin-top:16px">Other possibilities</h3><ul class="small">${d.top.slice(1).map((x) => `<li>${esc(labelName(x.label))} · ${pct(x.p)}</li>`).join("")}</ul>` : ""}
      ${leaves.length ? `<h3 style="margin-top:16px">Per-leaf results</h3><ol class="small">${leaves.map((l) => `<li>${esc(labelName(l.label))} · ${pct(l.p)}${l.status === "uncertain" ? " · uncertain" : ""}</li>`).join("")}</ol>` : ""}
      <h3 style="margin-top:16px">Review</h3>
      <div class="stack" style="margin-top:8px">
        <label class="field">Note<textarea id="note" maxlength="500" ${local ? "" : "disabled"}>${esc(d.review_note || "")}</textarea></label>
        <div class="row"><button class="btn btn--sm" data-review="confirmed" ${local ? "" : "disabled"}>${ICON("check")}Confirm</button>
          <button class="btn btn--ghost btn--sm" data-review="dismissed" ${local ? "" : "disabled"}>Dismiss (false alarm)</button>
          <button class="btn btn--ghost btn--sm" data-review="new" ${local ? "" : "disabled"}>Reset</button></div>
        <p class="small muted">Current: ${esc(d.review_status)}${local ? "" : " · reviewing needs the local network"}</p></div>`;
    drawer.dataset.open = "true";
    drawer.focus();
    $("#close").onclick = close;
    drawer.querySelectorAll("[data-review]").forEach((b) => b.addEventListener("click", async () => {
      try {
        await api(`/api/detections/${encodeURIComponent(id)}`, { method: "PATCH", body: { review_status: b.dataset.review, note: $("#note").value } });
        toast("Review saved.", "ok"); await load(); open(id);
      } catch (e) { toast(e.message, "error"); }
    }));
  }
  function close() { drawer.dataset.open = "false"; root.querySelectorAll("tbody tr").forEach((tr) => tr.removeAttribute("aria-selected")); }
  const onKey = (e) => { if (e.key === "Escape" && drawer.dataset.open === "true") close(); };
  document.addEventListener("keydown", onKey);

  $("#rows").addEventListener("click", (e) => { const tr = e.target.closest("tr[data-id]"); if (tr) open(tr.dataset.id); });
  $("#rows").addEventListener("keydown", (e) => { if (e.key === "Enter") { const tr = e.target.closest("tr[data-id]"); if (tr) open(tr.dataset.id); } });
  root.querySelector("thead").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-sort]");
    if (!b) return;
    order = sort === b.dataset.sort && order === "desc" ? "asc" : "desc";
    sort = b.dataset.sort; offset = 0; load();
  });
  let deb;
  $("#q").addEventListener("input", () => { clearTimeout(deb); deb = setTimeout(() => { offset = 0; load(); }, 300); });
  ["#kind", "#label", "#field", "#mission", "#from", "#to", "#review"].forEach((id) => $(id).addEventListener("change", () => { offset = 0; load(); }));
  $("#reset").onclick = () => { $("#filters").reset(); offset = 0; load(); };
  $("#prev").onclick = () => { offset = Math.max(0, offset - PAGE); load(); };
  $("#next").onclick = () => { offset += PAGE; load(); };
  await load();
  if (params.id) open(params.id);
  return () => document.removeEventListener("keydown", onKey);
}
