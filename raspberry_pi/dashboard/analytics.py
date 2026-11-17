"""Figures derived from stored data only. When the data cannot support a figure, the value is
None and a plain-language `reason` says why (the UI shows that instead of a number)."""
import json
import math
import time
from collections import Counter, defaultdict

from geo import LocalFrame, centroid, point_in_polygon
from planner import swath_m
from store import PROBLEM_KINDS

MIN_LEAVES = 20          # below this a health percentage is not meaningful
DAY = 86400
GUIDANCE = {  # general guidance only; shown with "confirm with an agronomist"
    "bacterial_spot": "Remove badly spotted leaves; avoid overhead watering; copper sprays are commonly used preventively.",
    "early_blight": "Remove lower infected leaves; mulch to stop soil splash; rotate crops; fungicide if spreading.",
    "late_blight": "Act quickly: isolate or remove infected plants; spreads fast in cool, wet weather.",
    "leaf_mold": "Improve ventilation and lower humidity (greenhouse); remove affected leaves.",
    "septoria_leaf_spot": "Remove spotted lower leaves; avoid wetting foliage; rotate crops.",
    "spider_mites": "Check leaf undersides; raise humidity; miticide or predatory mites if many.",
    "target_spot": "Remove infected leaves; improve airflow; fungicide if spreading.",
    "yellow_leaf_curl_virus": "Spread by whiteflies: control whiteflies and remove infected plants.",
    "mosaic_virus": "No cure: remove infected plants; disinfect tools and hands.",
    "leaf_miner": "Remove mined leaves; sticky traps; check for larvae trails.",
    "spotted_wilt_virus": "Spread by thrips: control thrips and remove infected plants.",
    "magnesium_deficiency": "Yellowing between veins on older leaves: check soil magnesium.",
    "nitrogen_deficiency": "Older leaves yellowing evenly: check soil nitrogen and fertiliser schedule.",
    "potassium_deficiency": "Leaf edge scorch on older leaves: check soil potassium.",
}


def guidance(label):
    return GUIDANCE.get(label)


def health_from_rows(rows):
    h = sum(r["n_healthy"] for r in rows)
    p = sum(r["n_problem"] for r in rows)
    u = sum(r["n_uncertain"] for r in rows)
    n = h + p
    if n < MIN_LEAVES:
        return {"pct": None, "healthy": h, "problem": p, "uncertain": u,
                "reason": f"Not enough data: {n} confidently classified leaves (need {MIN_LEAVES})."}
    return {"pct": round(100 * h / n, 1), "healthy": h, "problem": p, "uncertain": u, "reason": None}


def _mission_rows(store, mid):
    return store.q("SELECT * FROM detections WHERE mission_id=?", (mid,))


def latest_inspections(store, field_id, n=2):
    return store.q("SELECT * FROM missions WHERE field_id=? AND status IN ('completed','aborted') "
                   "AND EXISTS (SELECT 1 FROM detections d WHERE d.mission_id=missions.id) "
                   "ORDER BY started DESC LIMIT ?", (field_id, n))


def field_summary(store, field):
    insp = latest_inspections(store, field["id"])
    out = {"id": field["id"], "name": field["name"], "area_m2": field["area_m2"], "crop": field["crop"],
           "last_inspection": None, "health": None, "change": None, "problems": {}}
    if not insp:
        out["health"] = {"pct": None, "reason": "No inspection with captures yet."}
        return out
    cur = insp[0]
    rows = _mission_rows(store, cur["id"])
    out["last_inspection"] = {"mission_id": cur["id"], "t": cur["started"], "status": cur["status"],
                              "captures": len(rows)}
    out["health"] = health_from_rows(rows)
    out["problems"] = dict(Counter(r["label"] for r in rows
                                   if r["kind"] in PROBLEM_KINDS and r["status"] == "confident"))
    if len(insp) > 1:
        prev_rows = _mission_rows(store, insp[1]["id"])
        prev = health_from_rows(prev_rows)
        prev_problems = Counter(r["label"] for r in prev_rows if r["kind"] in PROBLEM_KINDS and r["status"] == "confident")
        labels = set(prev_problems) | set(out["problems"])
        out["change"] = {
            "previous_mission_id": insp[1]["id"], "previous_t": insp[1]["started"],
            "health_delta": round(out["health"]["pct"] - prev["pct"], 1)
            if out["health"]["pct"] is not None and prev["pct"] is not None else None,
            "reason": None if out["health"]["pct"] is not None and prev["pct"] is not None
            else "One of the two inspections has too few leaves to compare.",
            "problems": {l: {"before": prev_problems.get(l, 0), "now": out["problems"].get(l, 0)} for l in sorted(labels)},
        }
    return out


def farm_health(store, fields):
    """Health over the latest inspection of every field; falls back to the last 7 days of
    manual snapshots when no field has been inspected yet (the basis is reported)."""
    rows, basis = [], None
    for f in fields:
        insp = latest_inspections(store, f["id"], 1)
        if insp:
            rows += _mission_rows(store, insp[0]["id"])
    if rows:
        basis = "Latest inspection of each field"
    else:
        rows = store.q("SELECT * FROM detections WHERE t>=?", (time.time() - 7 * DAY,))
        basis = "Snapshots from the last 7 days (no field inspections yet)"
    h = health_from_rows(rows)
    h["basis"] = basis
    return h


def coverage(field, track, hfov_deg, min_alt_frac=0.6, plan_alt=None):
    """Share of the field's 5 m cells that came within half a camera swath of the flown track.
    Only track points at >= 60 % of the planned altitude count (climb/descent excluded)."""
    poly = field["polygon"]
    frame = LocalFrame(*centroid(poly))
    xy = [frame.to_xy(*p) for p in poly]
    xs, ys = [p[0] for p in xy], [p[1] for p in xy]
    area = abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(xy, xy[1:] + xy[:1]))) / 2
    cell = max(5.0, math.sqrt(area / 20000))          # keep at most ~20k cells
    cells = set()
    for i in range(int((max(xs) - min(xs)) / cell) + 1):
        for j in range(int((max(ys) - min(ys)) / cell) + 1):
            cx, cy = min(xs) + (i + 0.5) * cell, min(ys) + (j + 0.5) * cell
            if point_in_polygon(cx, cy, xy):
                cells.add((i, j))
    if not cells:
        return None
    covered = set()
    pts = [(frame.to_xy(t["lat"], t["lon"]), t["alt"] or 0) for t in track if t["lat"] is not None]
    for (a, alt_a), (b, _) in zip(pts, pts[1:]):
        if plan_alt and alt_a < plan_alt * min_alt_frac:
            continue
        r = swath_m(alt_a, hfov_deg) / 2
        seg = math.hypot(b[0] - a[0], b[1] - a[1])
        steps = max(1, int(seg / (cell / 2)))
        for k in range(steps + 1):
            px, py = a[0] + (b[0] - a[0]) * k / steps, a[1] + (b[1] - a[1]) * k / steps
            ri = int(r / cell) + 1
            ci, cj = int((px - min(xs)) / cell), int((py - min(ys)) / cell)
            for i in range(ci - ri, ci + ri + 1):
                for j in range(cj - ri, cj + ri + 1):
                    if (i, j) in cells and (i, j) not in covered:
                        cx, cy = min(xs) + (i + 0.5) * cell, min(ys) + (j + 0.5) * cell
                        if math.hypot(cx - px, cy - py) <= r:
                            covered.add((i, j))
    return {"pct": round(100 * len(covered) / len(cells), 1), "cell_m": round(cell, 1),
            "covered_cells": [[i, j] for i, j in covered], "origin": frame.to_latlon(min(xs), min(ys)),
            "assumption": f"Camera field of view {hfov_deg:g}° (setting); straight-down camera."}


def coverage_cells_geojson(cov, field):
    """Covered cells as small polygons for the map."""
    if not cov:
        return []
    frame = LocalFrame(*centroid(field["polygon"]))
    ox, oy = frame.to_xy(*cov["origin"])
    c, out = cov["cell_m"], []
    for i, j in cov["covered_cells"]:
        x0, y0 = ox + i * c, oy + j * c
        out.append([frame.to_latlon(x0, y0), frame.to_latlon(x0 + c, y0),
                     frame.to_latlon(x0 + c, y0 + c), frame.to_latlon(x0, y0 + c)])
    return out


def health_trend(store):
    pts = []
    for m in store.q("SELECT * FROM missions WHERE status IN ('completed','aborted') ORDER BY started"):
        rows = _mission_rows(store, m["id"])
        if rows:
            h = health_from_rows(rows)
            pts.append({"t": m["started"], "mission_id": m["id"], "field_id": m["field_id"], **h})
    daily = defaultdict(list)
    for r in store.q("SELECT * FROM detections WHERE source='manual' ORDER BY t"):
        daily[int(r["t"] // DAY) * DAY].append(r)
    manual = [{"t": d, **health_from_rows(rows)} for d, rows in sorted(daily.items())]
    return {"inspections": pts, "manual_daily": manual, "min_leaves": MIN_LEAVES}


def disease_trend(store, days=30):
    since = (int(time.time() // DAY) - days + 1) * DAY
    rows = store.q("SELECT t, label, kind FROM detections WHERE t>=? AND status='confident' AND kind IN "
                   "('disease','deficiency','pest')", (since,))
    labels = sorted({r["label"] for r in rows})
    series = {l: [0] * days for l in labels}
    for r in rows:
        series[r["label"]][min(days - 1, int((r["t"] - since) // DAY))] += 1
    return {"start": since, "days": days, "series": series, "total": len(rows)}


def distribution(store, t_from=None):
    args, where = [], "status='confident'"
    if t_from:
        where += " AND t>=?"
        args.append(t_from)
    rows = store.q(f"SELECT label, kind, COUNT(*) AS n FROM detections WHERE {where} GROUP BY label, kind "
                   "ORDER BY n DESC", args)
    leaves = store.one("SELECT SUM(n_healthy) AS h, SUM(n_problem) AS p, SUM(n_uncertain) AS u "
                       "FROM detections" + (" WHERE t>=?" if t_from else ""), args)
    return {"by_label": rows, "leaves": {"healthy": leaves["h"] or 0, "problem": leaves["p"] or 0,
                                         "uncertain": leaves["u"] or 0}}


def attention_areas(store, days=30, cell_m=10):
    """Clusters of confident problem detections with a map position (last `days`)."""
    rows = store.q("SELECT * FROM detections WHERE t>=? AND lat IS NOT NULL AND status='confident' AND kind IN "
                   "('disease','deficiency','pest')", (time.time() - days * DAY,))
    if not rows:
        return []
    frame = LocalFrame(rows[0]["lat"], rows[0]["lon"])
    groups = defaultdict(list)
    for r in rows:
        x, y = frame.to_xy(r["lat"], r["lon"])
        groups[(int(x // cell_m), int(y // cell_m))].append(r)
    out = []
    for g in groups.values():
        labels = Counter(r["label"] for r in g)
        lat, lon = centroid([(r["lat"], r["lon"]) for r in g])
        out.append({"lat": round(lat, 6), "lon": round(lon, 6), "count": len(g), "top_label": labels.most_common(1)[0][0],
                    "labels": dict(labels), "field_id": g[0]["field_id"], "last_t": max(r["t"] for r in g),
                    "ids": [r["id"] for r in g][:50]})
    return sorted(out, key=lambda a: -a["count"])[:20]


def mission_stats(store):
    ms = store.q("SELECT * FROM missions ORDER BY created")
    flown = [m for m in ms if m["status"] in ("completed", "aborted")]
    by_status = Counter(m["status"] for m in ms)
    stats = [json.loads(m["stats"]) for m in flown if m["stats"]]
    dur = [s["duration_s"] for s in stats if s.get("duration_s")]
    return {"total": len(ms), "by_status": dict(by_status), "flown": len(flown),
            "completed": by_status.get("completed", 0), "flight_time_s": sum(dur),
            "avg_duration_s": round(sum(dur) / len(dur)) if dur else None,
            "distance_m": sum(s.get("distance_m") or 0 for s in stats),
            "captures": sum(s.get("captures") or 0 for s in stats),
            "per_mission": [{"id": m["id"], "t": m["started"], "status": m["status"], "field_id": m["field_id"],
                             **(json.loads(m["stats"]) if m["stats"] else {})} for m in flown][-30:]}


def by_field(store, fields):
    out = []
    for f in fields:
        rows = store.q("SELECT label, COUNT(*) AS n FROM detections WHERE field_id=? AND status='confident' AND "
                       "kind IN ('disease','deficiency','pest') GROUP BY label", (f["id"],))
        out.append({"field_id": f["id"], "name": f["name"], "problems": {r["label"]: r["n"] for r in rows},
                    "total": sum(r["n"] for r in rows)})
    return out


def heat_points(store, days=90):
    rows = store.q("SELECT lat, lon, label, kind FROM detections WHERE t>=? AND lat IS NOT NULL AND "
                   "status='confident' AND kind IN ('disease','deficiency','pest')", (time.time() - days * DAY,))
    return [[r["lat"], r["lon"], r["label"]] for r in rows]
