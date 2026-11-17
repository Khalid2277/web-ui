#!/usr/bin/env python3
"""Farm command center for the crop-disease drone, served from the Raspberry Pi.

  /           farmer dashboard (overview, live operations, map, disease log, analytics, missions, system)
  /console    the original single-page live console (unchanged)

Streams the camera, runs the leaf-disease model, keeps fields / missions / detections in SQLite,
plans and flies autonomous inspection missions over MAVLink (every command waits for the
autopilot's acknowledgement), and reports companion-computer health.

Drone commands are accepted only from the local network (never through the public tunnel).

Run:  python3 raspberry_pi/dashboard/app.py      then open http://<pi-ip>:8000
"""
import csv
import hmac
import io
import ipaddress
import json
import os
import time
from functools import wraps
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, send_from_directory

import analytics
from config import AI_INTERVAL_S, CAM_H, CAM_W, CAPTURE_DIR, DEV, HOST, LOG_FILE, PORT, now
from connection import Connection
from devices import Analyzer, Camera, FolderCamera, NoCamera, SensorStore, SystemHealth
from geo import validate_polygon
from missions import MissionController, MissionError, analyse_and_store
from store import DEFAULT_SETTINGS, PROBLEM_KINDS

app = Flask(__name__)
app.json.sort_keys = False

# ---------------------------------------------------------------- password (unchanged behaviour)
PASSWORD_FILE = Path.home() / ".config" / "crop-console" / "password"


def console_password():
    if DEV:
        return None
    try:
        return PASSWORD_FILE.read_text().strip() or None
    except OSError:
        return None


MAX_FAILURES, LOCKOUT_S = 10, 15 * 60
failures = {}  # client address -> (count, first failure time)


@app.before_request
def require_password():
    password = console_password()
    if password is None:
        return None
    # Behind the Cloudflare tunnel every request arrives from localhost; the real
    # visitor address is in CF-Connecting-IP.
    client = request.headers.get("CF-Connecting-IP") or request.remote_addr
    count, since = failures.get(client, (0, now()))
    if count >= MAX_FAILURES and now() - since < LOCKOUT_S:
        return Response("Too many wrong passwords. Try again in 15 minutes.", 429)
    auth = request.authorization
    if auth and hmac.compare_digest(auth.password or "", password):
        failures.pop(client, None)
        return None
    if auth:  # a wrong password, not just the browser's first unauthenticated request
        if now() - since >= LOCKOUT_S:
            count, since = 0, now()
        failures[client] = (count + 1, since)
    return Response("Password required.", 401, {"WWW-Authenticate": 'Basic realm="Crop Health Console"'})


# ---------------------------------------------------------------- local-network guard
def request_is_local():
    """True only for direct requests from a private/loopback address. Anything that came through
    the Cloudflare tunnel (CF-Connecting-IP) or another proxy (X-Forwarded-For) is remote."""
    if request.headers.get("CF-Connecting-IP") or request.headers.get("X-Forwarded-For"):
        return False
    try:
        ip = ipaddress.ip_address(request.remote_addr or "")
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback


def local_only(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not request_is_local():
            return jsonify(error="View-only: changes and drone commands are only accepted from the drone's "
                                 "local Wi-Fi network, not through the public link.", code="remote"), 403
        return fn(*a, **kw)
    return wrapper


def actor():
    return request.headers.get("CF-Connecting-IP") or request.remote_addr or "unknown"


# ---------------------------------------------------------------- workers
dev_images = os.environ.get("CONSOLE_DEV_IMAGES")
camera = FolderCamera(dev_images) if DEV and dev_images else NoCamera() if DEV else Camera()
sensors = SensorStore()
analyzer = Analyzer(camera, sensors)
health = SystemHealth()
conn = Connection()                 # simulator or real drone; switchable at runtime
link, store = conn.link, conn.store  # proxies that always point at the current mode
imported = conn.stores["real"].import_jsonl(LOG_FILE)   # legacy snapshots are real records
controller = MissionController(conn, camera, analyzer)


def telemetry():
    if not conn.enabled:
        return {"link": False, "link_error": f"No {'simulator' if conn.sim else 'drone'} address configured."}
    return link.snapshot()


def body():
    return request.get_json(silent=True) or {}


def err(message, code=400, **extra):
    return jsonify(error=message, **extra), code


@app.errorhandler(MissionError)
def mission_error(exc):
    return jsonify(error=str(exc), detail=exc.data), exc.code


# ---------------------------------------------------------------- pages
@app.route("/")
def farm():
    return render_template("farm.html")


@app.route("/console")
def index():
    return render_template("index.html")


@app.route("/stream.mjpg")
def stream():
    # Remote viewers request a low frame rate so the stream doesn't eat mobile data.
    try:
        fps = min(20.0, max(1.0, float(request.args.get("fps", 20))))
    except ValueError:
        fps = 20.0

    def frames():
        last = None
        while True:
            jpeg = camera.jpeg
            if jpeg is not None and jpeg is not last:
                last = jpeg
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
            time.sleep(1 / fps)
    return Response(frames(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/frame.jpg")
def frame_jpg():
    """Latest camera frame (used as the dashboard hero image)."""
    if camera.jpeg is None:
        return err("No camera frame available.", 404)
    return Response(camera.jpeg, mimetype="image/jpeg", headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------- live state (SSE)
def strip_probs(ai):
    """The full probability vectors are only needed server-side; keep the SSE payload small."""
    clean = lambda x: {k: v for k, v in x.items() if k != "probs"} if x else x
    return {**ai, "leaves": [clean(l) for l in ai["leaves"]], "frame": clean(ai["frame"]),
            "headline": clean(ai["headline"])}


def model_card():
    c = analyzer.card
    m = (c.get("metrics") or {}).get("held_out_field_test") or {}
    return {"name": c.get("name"), "version": c.get("version"),
            "architecture": (c.get("classifier") or {}).get("architecture"),
            "detector": (c.get("detector") or {}).get("architecture"),
            "field_macro_f1": m.get("field_macro_f1"), "field_accuracy": m.get("field_accuracy"),
            "false_disease_rate": m.get("false_disease_rate_on_negatives"),
            "lab_only_classes": c.get("lab_only_classes", []), "classes": c.get("classes", [])}


def state(local=None, light=False):
    ai = analyzer.result
    tel = telemetry()
    if light:
        tel.pop("history", None)
    return {
        "t": now(),
        "camera": {"status": camera.status, "fps": round(camera.fps, 1), "width": CAM_W, "height": CAM_H,
                   "frame_age_s": round(now() - camera.frame_time, 1) if camera.frame_time else None},
        "ai": strip_probs(ai) if ai and now() - ai["t"] < 10 else None,
        "model": {"classes": analyzer.classes, "interval_s": AI_INTERVAL_S, "card": model_card()},
        "env": sensors.summary(),
        "telemetry": tel,
        "telemetry_enabled": conn.enabled, "mode": conn.mode,
        "system": health.state,
        "detections": store.one("SELECT COUNT(*) AS n FROM detections")["n"],
        "mission": controller.status(),
        "sim": conn.sim, "dev": DEV, "local": local,
    }


@app.route("/events")
def events():
    local = request_is_local()
    light = request.args.get("light") == "1"

    def stream_state():
        while True:
            yield f"data: {json.dumps(state(local, light))}\n\n"
            time.sleep(0.5)
    return Response(stream_state(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/state")
def api_state():
    return jsonify(state(request_is_local()))


# ---------------------------------------------------------------- manual snapshot (old console + dashboard)
@app.route("/api/snapshot", methods=["POST"])
def api_snapshot():
    frame = camera.frame
    if frame is None:
        return err("The camera has no frame yet. Check that it is connected, then try again.", 503)
    b = body()
    truth = b.get("truth")  # test mode: what the item in front of the camera really is
    if truth and truth not in analyzer.classes:
        return err(f"Unknown label '{truth}'. Pick one of the listed classes.")
    tel = telemetry() if conn.enabled else {}
    d = analyse_and_store(store, analyzer, frame, tel, source="manual",
                          extra={"truth": truth, "test_item": (b.get("item") or "")[:80] or None})
    entry = {**d, "alt_rel": d["alt"]}
    with open(LOG_FILE, "a") as f:  # keep the legacy log too, so the old console stays compatible
        f.write(json.dumps({k: entry[k] for k in ("id", "t", "image", "thumb", "label", "p", "status", "top", "leaves",
                                                  "truth", "test_item", "model", "lat", "lon", "alt_rel", "mode")}) + "\n")
        f.flush()
        os.fsync(f.fileno())
    return jsonify(entry)


# ---------------------------------------------------------------- detections
def det_filters(a):
    def num(k):
        try:
            return float(a[k]) if a.get(k) not in (None, "") else None
        except ValueError:
            return None
    return dict(kind=a.get("kind") or None, label=a.get("label") or None, field_id=a.get("field") or None,
                mission_id=a.get("mission") or None, t_from=num("from"), t_to=num("to"),
                review=a.get("review") or None, q=(a.get("q") or "").strip() or None, min_p=num("min_p"),
                mapped_only=a.get("mapped") == "1", sort=a.get("sort", "t"), order=a.get("order", "desc"))


@app.route("/api/detections")
def api_detections_legacy():
    """Old console format: a plain list, newest first."""
    _, rows = store.detections(limit=200)
    return jsonify([{**r, "alt_rel": r["alt"]} for r in rows])


@app.route("/api/log")
def api_log():
    try:
        limit = max(1, min(500, int(request.args.get("limit", 100))))
        offset = max(0, int(request.args.get("offset", 0)))
    except ValueError:
        return err("limit and offset must be whole numbers.")
    total, rows = store.detections(**det_filters(request.args), limit=limit, offset=offset)
    for r in rows:
        r["guidance"] = analytics.guidance(r["label"])
    return jsonify(total=total, limit=limit, offset=offset, items=rows)


@app.route("/api/detections/<did>", methods=["GET"])
def api_detection(did):
    d = store.detection(did)
    if not d:
        return err("Detection not found.", 404)
    d["guidance"] = analytics.guidance(d["label"])
    d["field"] = store.field(d["field_id"]) if d["field_id"] else None
    return jsonify(d)


@app.route("/api/detections/<did>", methods=["PATCH"])
@local_only
def api_detection_review(did):
    b = body()
    status = b.get("review_status")
    if status not in ("new", "confirmed", "dismissed"):
        return err("review_status must be new, confirmed or dismissed.")
    if not store.detection(did):
        return err("Detection not found.", 404)
    return jsonify(store.review_detection(did, status, (b.get("note") or "")[:500] or None))


@app.route("/api/detections.csv")
def api_detections_csv():
    _, rows = store.detections(**det_filters(request.args), limit=100000)
    fields = {f["id"]: f["name"] for f in store.fields(include_archived=True)}
    cols = ["id", "time_utc", "source", "field", "mission_id", "label", "kind", "confidence", "status",
            "leaves", "healthy_leaves", "problem_leaves", "uncertain_leaves", "lat", "lon", "gps_accuracy_m",
            "altitude_m", "review_status", "review_note", "image"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for r in rows:
        w.writerow([r["id"], time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(r["t"])), r["source"],
                    fields.get(r["field_id"], ""), r["mission_id"] or "", r["label"], r["kind"],
                    "" if r["p"] is None else f"{r['p']:.3f}", r["status"] or "", r["n_leaves"], r["n_healthy"],
                    r["n_problem"], r["n_uncertain"], "" if r["lat"] is None else f"{r['lat']:.6f}",
                    "" if r["lon"] is None else f"{r['lon']:.6f}", r["h_acc_m"] or "", r["alt"] or "",
                    r["review_status"], r["review_note"] or "", r["image"] or ""])
    name = f"detections-{time.strftime('%Y%m%d-%H%M')}.csv"
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ---------------------------------------------------------------- fields
@app.route("/api/fields")
def api_fields():
    fields = store.fields(include_archived=request.args.get("archived") == "1")
    return jsonify([{**f, "summary": analytics.field_summary(store, f)} for f in fields])


@app.route("/api/fields", methods=["POST"])
@local_only
def api_field_add():
    b = body()
    name = (b.get("name") or "").strip()[:60]
    if not name:
        return err("Give the field a name.")
    problem = validate_polygon(b.get("polygon"))
    if problem:
        return err(problem)
    polygon = [[round(float(p[0]), 7), round(float(p[1]), 7)] for p in b["polygon"]]
    return jsonify(store.add_field(name, polygon, (b.get("crop") or "tomato")[:30], (b.get("notes") or "")[:500] or None)), 201


@app.route("/api/fields/<fid>", methods=["PATCH"])
@local_only
def api_field_update(fid):
    if not store.field(fid):
        return err("Field not found.", 404)
    b, kw = body(), {}
    if "name" in b:
        if not str(b["name"]).strip():
            return err("The name cannot be empty.")
        kw["name"] = str(b["name"]).strip()[:60]
    for k in ("crop", "notes"):
        if k in b:
            kw[k] = (str(b[k]) if b[k] is not None else None)
    if "polygon" in b:
        problem = validate_polygon(b["polygon"])
        if problem:
            return err(problem)
        kw["polygon"] = [[round(float(p[0]), 7), round(float(p[1]), 7)] for p in b["polygon"]]
    if "archived" in b:
        kw["archived"] = bool(b["archived"])
    return jsonify(store.update_field(fid, **kw))


@app.route("/api/fields/<fid>", methods=["DELETE"])
@local_only
def api_field_delete(fid):
    if not store.field(fid):
        return err("Field not found.", 404)
    if store.field_has_history(fid):
        store.update_field(fid, archived=True)
        return jsonify(archived=True, message="The field has missions or detections, so it was archived (kept for history).")
    store.delete_field(fid)
    return jsonify(deleted=True)


# ---------------------------------------------------------------- missions
@app.route("/api/missions/plan", methods=["POST"])
def api_mission_plan():
    b = body()
    field = store.field(b.get("field_id") or "")
    if not field:
        return err("Choose a field first.", 404)
    plan = controller.plan(field, b)
    return jsonify({k: v for k, v in plan.items() if k != "items"} | {"n_items": len(plan["items"])})


@app.route("/api/missions", methods=["POST"])
@local_only
def api_mission_create():
    b = body()
    return jsonify(controller.create(b.get("field_id") or "", b)), 201


@app.route("/api/missions")
def api_missions():
    fields = {f["id"]: f["name"] for f in store.fields(include_archived=True)}
    out = []
    for m in store.missions(limit=int(request.args.get("limit", 200)), field_id=request.args.get("field") or None):
        n = store.one("SELECT COUNT(*) AS n, SUM(kind IN ('disease','deficiency','pest') AND status='confident') AS p "
                      "FROM detections WHERE mission_id=?", (m["id"],))
        plan = m.pop("plan")
        out.append({**m, "field_name": fields.get(m["field_id"]), "captures": n["n"], "problems": n["p"] or 0,
                    "plan_summary": {k: plan.get(k) for k in ("lines", "total_length_m", "est_duration_s",
                                                              "est_battery_pct", "swath_m", "params", "verified")}})
    return jsonify(out)


_coverage_cache = {}


def mission_coverage(m, field):
    key = (m["id"], m["status"], m["ended"])
    if key not in _coverage_cache:
        track = store.track(m["id"])
        cov = analytics.coverage(field, track, m["plan"]["params"]["hfov_deg"],
                                 plan_alt=m["plan"]["params"]["altitude_m"]) if track and field else None
        _coverage_cache[key] = (cov, analytics.coverage_cells_geojson(cov, field) if cov else [])
    return _coverage_cache[key]


@app.route("/api/missions/<mid>")
def api_mission(mid):
    m = store.mission(mid)
    if not m:
        return err("Mission not found.", 404)
    field = store.field(m["field_id"]) if m["field_id"] else None
    m["plan"].pop("items", None)
    cov, cells = mission_coverage(m, field) if m["status"] in ("completed", "aborted") else (None, [])
    _, dets = store.detections(mission_id=mid, limit=2000, order="asc")
    return jsonify({**m, "field": field, "events": store.events(mid, 500), "track": store.track(mid),
                    "detections": dets, "health": analytics.health_from_rows(dets),
                    "coverage": {k: v for k, v in cov.items() if k != "covered_cells"} if cov else None,
                    "coverage_cells": cells})


@app.route("/api/missions/<mid>", methods=["DELETE"])
@local_only
def api_mission_delete(mid):
    m = store.mission(mid)
    if not m:
        return err("Mission not found.", 404)
    if m["status"] not in ("planned", "uploaded") or store.track(mid):
        return err("Only missions that never flew can be deleted; flown missions are kept for history.", 409)
    store.x("DELETE FROM mission_events WHERE mission_id=?", (mid,))
    store.x("DELETE FROM missions WHERE id=?", (mid,))
    return jsonify(deleted=True)


@app.route("/api/missions/<mid>/upload", methods=["POST"])
@local_only
def api_mission_upload(mid):
    return jsonify(controller.upload(mid, actor()))


@app.route("/api/missions/<mid>/preflight")
def api_mission_preflight(mid):
    return jsonify(controller.preflight(mid, request_is_local()))


@app.route("/api/missions/<mid>/start", methods=["POST"])
@local_only
def api_mission_start(mid):
    b = body()
    if b.get("confirm") is not True:
        return err("Starting a mission needs explicit confirmation.")
    return jsonify(controller.launch(mid, b.get("token"), request_is_local(), actor()))


@app.route("/api/mission/<action>", methods=["POST"])
@local_only
def api_flight_command(action):
    if action not in ("pause", "resume", "rtl", "land"):
        return err("Unknown command.", 404)
    if body().get("confirm") is not True:
        return err("This command needs explicit confirmation.")
    res = controller.flight_command(action, actor())
    return jsonify(res), 200 if res["ok"] else 502


@app.route("/api/mission/events")
def api_mission_events():
    return jsonify(store.events(request.args.get("mission") or None, 100))


# ---------------------------------------------------------------- connection (simulator / real drone)
@app.route("/api/connection")
def api_connection():
    local = request_is_local()
    return jsonify({**conn.info(local), "blocked": conn.blocked_reason(bool(controller.active_id))})


@app.route("/api/connection", methods=["POST"])
@local_only
def api_connection_switch():
    b = body()
    mode = b.get("mode")
    if mode == "real" and b.get("confirm") is not True:
        return err("Switching to the real drone needs explicit confirmation.")
    try:
        info = conn.switch(mode, busy=bool(controller.active_id))
    except ValueError as exc:
        return err(str(exc))
    except RuntimeError as exc:
        return err(str(exc), 409)
    _coverage_cache.clear()
    return jsonify(info)


@app.route("/api/connection", methods=["PUT"])
@local_only
def api_connection_urls():
    b, info = body(), None
    try:
        for mode in ("sim", "real"):
            key = f"{mode}_url"
            if key in b:
                info = conn.set_url(mode, (b[key] or "").strip() or None, busy=bool(controller.active_id))
    except ValueError as exc:
        return err(str(exc))
    except RuntimeError as exc:
        return err(str(exc), 409)
    return jsonify(info or conn.info())


# ---------------------------------------------------------------- settings
SETTING_RANGES = {"endurance_min": (1, 60), "min_battery_v": (6, 30), "camera_hfov_deg": (10, 150),
                  "default_altitude_m": (3, 60), "default_speed_ms": (1, 10), "sidelap": (0, 0.8),
                  "max_hdop": (0.5, 5), "min_sats": (4, 20)}


@app.route("/api/settings")
def api_settings():
    s = store.settings()
    return jsonify({k: s[k] for k in DEFAULT_SETTINGS})


@app.route("/api/settings", methods=["PUT"])
@local_only
def api_settings_put():
    b, changes = body(), {}
    for k, v in b.items():
        if k == "farm_name":
            if not str(v).strip():
                return err("The farm name cannot be empty.")
            changes[k] = str(v).strip()[:60]
        elif k in SETTING_RANGES:
            lo, hi = SETTING_RANGES[k]
            try:
                v = float(v)
            except (TypeError, ValueError):
                return err(f"{k} must be a number.")
            if not lo <= v <= hi:
                return err(f"{k} must be between {lo:g} and {hi:g}.")
            changes[k] = v
    store.set_settings(changes)
    return api_settings()


# ---------------------------------------------------------------- overview / analytics / map / system
def alerts():
    out = []
    t = now()
    for d in store.q("SELECT * FROM detections WHERE t>=? AND status='confident' AND kind IN ('disease','deficiency','pest') "
                     "AND review_status!='dismissed' ORDER BY t DESC LIMIT 5", (t - 86400,)):
        out.append({"level": "warn", "kind": "detection", "t": d["t"], "id": d["id"],
                    "title": d["label"].replace("_", " ").capitalize() + " suspected",
                    "detail": f"{d['p'] * 100:.0f}% confidence" + ("" if d["lat"] is not None else ", no GPS position")})
    for m in store.q("SELECT * FROM missions WHERE status IN ('failed','aborted') AND ended>=? ORDER BY ended DESC LIMIT 3",
                     (t - 3 * 86400,)):
        out.append({"level": "error", "kind": "mission", "t": m["ended"], "id": m["id"],
                    "title": "Mission " + ("failed to start" if m["status"] == "failed" else "ended early"),
                    "detail": m["end_reason"] or ""})
    sysh = health.state
    if sysh.get("undervoltage_now"):
        out.append({"level": "error", "kind": "system", "t": t, "title": "Raspberry Pi under-voltage",
                    "detail": "The Pi's power supply is too weak right now."})
    if conn.enabled:
        s = link.snapshot()
        if s.get("link") and s.get("battery_v") is not None and s["battery_v"] < store.settings()["min_battery_v"]:
            out.append({"level": "warn", "kind": "drone", "t": t, "title": "Drone battery low",
                        "detail": f"{s['battery_v']} V"})
    return sorted(out, key=lambda a: -a["t"])[:10]


def hourly_activity():
    """Captures and confident problems per hour for the last 24 hours (index 0 = 23 h ago)."""
    t1 = int(now() // 3600 + 1) * 3600
    caps, probs = [0] * 24, [0] * 24
    for r in store.q("SELECT t, kind, status FROM detections WHERE t>=?", (t1 - 24 * 3600,)):
        i = min(23, int((r["t"] - (t1 - 24 * 3600)) // 3600))
        caps[i] += 1
        if r["status"] == "confident" and r["kind"] in PROBLEM_KINDS:
            probs[i] += 1
    return {"end": t1, "captures": caps, "problems": probs}


@app.route("/api/overview")
def api_overview():
    st = store.settings()
    fields = store.fields()
    last = store.one("SELECT * FROM missions WHERE status IN ('completed','aborted') ORDER BY started DESC LIMIT 1")
    week = now() - 7 * 86400
    counts = store.one("SELECT COUNT(*) AS total, SUM(t>=?) AS week, "
                       "SUM(kind IN ('disease','deficiency','pest') AND status='confident') AS problems, "
                       "SUM(kind IN ('disease','deficiency','pest') AND status='confident' AND t>=?) AS problems_week "
                       "FROM detections", (week, week))
    _, recent = store.detections(limit=6)
    return jsonify({
        "farm_name": st["farm_name"], "fields": len(fields),
        "area_m2": round(sum(f["area_m2"] for f in fields)) if fields else None,
        "health": analytics.farm_health(store, fields),
        "detections": {k: counts[k] or 0 for k in counts},
        "last_inspection": ({"mission_id": last["id"], "t": last["started"], "status": last["status"],
                             "field_id": last["field_id"], "stats": json.loads(last["stats"]) if last["stats"] else None}
                            if last else None),
        "alerts": alerts(), "recent": recent,
        "top_problems": [{**r, "guidance": analytics.guidance(r["label"])} for r in store.q(
            "SELECT label, kind, COUNT(*) AS n FROM detections WHERE t>=? AND status='confident' AND "
            "kind IN ('disease','deficiency','pest') GROUP BY label, kind ORDER BY n DESC LIMIT 4", (now() - 30 * 86400,))],
        "hourly": hourly_activity(),
        "missions": store.one("SELECT COUNT(*) AS n FROM missions WHERE status IN ('completed','aborted')")["n"],
    })


@app.route("/api/analytics")
def api_analytics():
    fields = store.fields()
    try:
        days = max(7, min(365, int(request.args.get("days", 30))))
    except ValueError:
        days = 30
    return jsonify({
        "health": analytics.health_trend(store), "diseases": analytics.disease_trend(store, days),
        "distribution": analytics.distribution(store, now() - days * 86400),
        "distribution_all": analytics.distribution(store),
        "fields": [analytics.field_summary(store, f) for f in fields], "by_field": analytics.by_field(store, fields),
        "attention": analytics.attention_areas(store, days), "missions": analytics.mission_stats(store),
        "farm_health": analytics.farm_health(store, fields), "days": days,
    })


@app.route("/api/map")
def api_map():
    fields = store.fields()
    out_fields = []
    for f in fields:
        insp = analytics.latest_inspections(store, f["id"], 1)
        cov, cells = (None, [])
        if insp:
            m = store.mission(insp[0]["id"])
            cov, cells = mission_coverage(m, f)
        out_fields.append({**f, "coverage": {k: v for k, v in cov.items() if k != "covered_cells"} if cov else None,
                           "coverage_cells": cells, "last_mission_id": insp[0]["id"] if insp else None})
    routes = []
    for m in store.missions(limit=20):
        if m["status"] in ("completed", "aborted") or m["status"] in ("planned", "uploaded") or m["id"] == controller.active_id:
            routes.append({"id": m["id"], "field_id": m["field_id"], "status": m["status"], "t": m["started"] or m["created"],
                           "waypoints": m["plan"]["waypoints"],
                           "track": [[p["lat"], p["lon"]] for p in store.track(m["id"])[::2] if p["lat"] is not None]})
    _, dets = store.detections(**{**det_filters(request.args), "mapped_only": True}, limit=5000)
    return jsonify({"fields": out_fields, "routes": routes, "detections": dets,
                    "home": telemetry().get("home")})


@app.route("/api/system")
def api_system():
    tel = telemetry()
    tel.pop("history", None)
    db_bytes = sum(p.stat().st_size for p in CAPTURE_DIR.glob("farm*.db*") if p.exists())
    last_det = store.one("SELECT MAX(t) AS t FROM detections")["t"]
    return jsonify({
        "sim": conn.sim, "dev": DEV, "telemetry_enabled": conn.enabled, "connection": conn.info(request_is_local()),
        "mavlink_url": conn.url if request_is_local() else conn.describe(),
        "telemetry": tel, "camera": {"status": camera.status, "fps": round(camera.fps, 1),
                                     "frame_age_s": round(now() - camera.frame_time, 1) if camera.frame_time else None},
        "pi": health.state, "model": model_card(), "sensors": {k: v for k, v in sensors.summary().items() if k != "series"},
        "database": {"bytes": db_bytes, "detections": store.one("SELECT COUNT(*) AS n FROM detections")["n"],
                     "missions": store.one("SELECT COUNT(*) AS n FROM missions")["n"], "last_detection_t": last_det,
                     "legacy_imported": imported},
        "local": request_is_local(), "server_t": now(),
    })


# ---------------------------------------------------------------- field sensors
@app.route("/api/sensors", methods=["POST"])
def api_sensors():
    """Field nodes post BME680 + soil readings, e.g.
    {"node": "bed-1", "temp_c": 24.1, "rh": 88, "pressure_hpa": 1009, "gas_ohm": 52000, "soil_moisture_pct": 41}
    A list of such objects is also accepted (a LoRa gateway forwarding a batch)."""
    b = request.get_json(silent=True)
    items = b if isinstance(b, list) else [b] if isinstance(b, dict) else None
    if not items:
        return err("Send a JSON object (or a list) with sensor readings.")
    try:
        saved = [sensors.add(x) for x in items[:500]]
    except (TypeError, ValueError) as exc:
        return err(str(exc))
    return jsonify(saved=len(saved), latest=saved[-1])


@app.route("/api/sensors", methods=["GET"])
def api_sensors_get():
    return jsonify(sensors.summary())


@app.route("/captures/<path:name>")
def captures(name):
    return send_from_directory(CAPTURE_DIR, name)


if __name__ == "__main__":
    for worker in (camera, analyzer, health, controller):
        worker.start()
    conn.start()
    app.run(host=HOST, port=PORT, threaded=True)
