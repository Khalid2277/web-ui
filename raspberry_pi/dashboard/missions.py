"""Autonomous inspection missions: plan -> upload+verify -> preflight -> start -> monitor -> end.

The mission stage is always derived from live telemetry (mode, armed, current mission item,
altitude), never assumed from the command we sent. Every command attempt and its autopilot
answer is written to mission_events.
"""
import threading
import time
from datetime import datetime, timezone

import cv2

import preflight
from config import CAPTURE_DIR, now
from geo import LocalFrame, distance_m, point_in_polygon
from mavlink_link import mission_matches
from planner import PlanError, plan_survey, route_progress

ACTIVE = ("starting", "takeoff", "surveying", "paused", "returning", "landing", "pilot")
STAGE_TEXT = {
    "planned": "Planned", "uploaded": "Uploaded and verified", "starting": "Starting",
    "takeoff": "Taking off", "surveying": "Surveying", "paused": "Paused (holding position)",
    "returning": "Returning to launch", "landing": "Landing", "pilot": "Pilot in control",
    "completed": "Completed", "aborted": "Ended early", "failed": "Failed to start",
}
TELEMETRY_MAX_AGE_S = 1.0


class MissionError(Exception):
    def __init__(self, message, code=409, data=None):
        super().__init__(message)
        self.code, self.data = code, data


class MissionController(threading.Thread):
    def __init__(self, conn, camera, analyzer):
        super().__init__(daemon=True)
        self.conn, self.camera, self.analyzer = conn, camera, analyzer
        self.store, self.link = conn.store, conn.link      # proxies: always the current mode
        self.tokens = preflight.Tokens()
        self.lock = threading.RLock()
        self.active_id = None
        self.live = {}              # per-flight runtime info for the SSE payload
        self.track_buf = []
        self.capture_busy = False
        self.commands = []          # recent command results (any mission), newest first
        self._resume()
        conn.listeners.append(self.on_switch)

    @property
    def telemetry_enabled(self):
        return self.conn.enabled

    def _resume(self):
        """Resume monitoring a mission that was in flight when the console (re)started."""
        store = self.store
        for m in store.missions(limit=20):
            if m["status"] in ACTIVE:
                self.active_id = m["id"]
                self._reset_live(m)
                cp = (m["stats"] or {}).get("checkpoint") or {}
                for k in ("max_seq", "max_frac", "start_v", "airborne", "max_alt"):
                    if k in cp:
                        self.live[k] = cp[k]
                if any((t["alt"] or 0) > 1.0 for t in store.track(m["id"])):
                    self.live["airborne"] = True
                store.event(m["id"], "warn", "server", "Console restarted during the mission; monitoring resumed.")
                break

    def on_switch(self):
        """Called after switching simulator <-> real drone: forget everything about the old link."""
        with self.lock:
            self.tokens = preflight.Tokens()
            preflight.clear_cache()
            self.active_id, self.live, self.track_buf, self.commands = None, {}, [], []
            self._resume()

    # ---------------------------------------------------------------- planning
    def plan(self, field, params):
        st = self.store.settings()
        try:
            return plan_survey(field["polygon"],
                               altitude_m=float(params.get("altitude_m", st["default_altitude_m"])),
                               speed_ms=float(params.get("speed_ms", st["default_speed_ms"])),
                               hfov_deg=float(st["camera_hfov_deg"]), sidelap=float(st["sidelap"]),
                               endurance_min=float(st["endurance_min"]))
        except (TypeError, ValueError) as exc:
            raise MissionError(str(exc), 400)

    def create(self, field_id, params):
        field = self.store.field(field_id)
        if not field or field["archived"]:
            raise MissionError("Choose an existing field first.", 404)
        plan = self.plan(field, params)
        m = self.store.add_mission(field_id, plan, self.conn.sim)
        self.store.event(m["id"], "info", "plan",
                         f"Planned {plan['lines']} survey lines, {plan['total_length_m']:.0f} m, "
                         f"about {plan['est_duration_s'] / 60:.1f} min.")
        return m

    def _get(self, mid):
        m = self.store.mission(mid)
        if not m:
            raise MissionError("Mission not found.", 404)
        return m

    def upload(self, mid, actor):
        with self.lock:
            if self.active_id:
                raise MissionError("Another mission is in progress. Wait for it to end.")
            m = self._get(mid)
            if m["status"] not in ("planned", "uploaded"):
                raise MissionError(f"A {m['status']} mission cannot be uploaded again. Plan a new one.")
            plan = m["plan"]
            self.store.event(mid, "info", "command", f"Uploading {len(plan['items'])} mission items ({actor}).")
            res = self.link.upload_mission(plan["items"])
            self._log_cmd(mid, "upload", res)
            if not res["ok"]:
                raise MissionError(f"Upload failed: {res['detail']}", 502, res)
            readback = self.link.download_mission()
            ok, detail = mission_matches(plan["items"], readback)
            self.store.event(mid, "info" if ok else "error", "verify", detail)
            if not ok:
                plan["verified"] = False
                self.store.update_mission(mid, plan=plan)
                raise MissionError(f"Verification failed: {detail}", 502)
            plan["verified"] = True
            # Any other mission marked as uploaded is no longer on the autopilot.
            for other in self.store.missions(limit=50):
                if other["id"] != mid and other["status"] == "uploaded":
                    other["plan"]["verified"] = False
                    self.store.update_mission(other["id"], status="planned", plan=other["plan"])
            return self.store.update_mission(mid, status="uploaded", uploaded=now(), plan=plan)

    def preflight(self, mid, is_local):
        m = self._get(mid)
        field = self.store.field(m["field_id"]) if m["field_id"] else None
        checks = preflight.run_checks(self.link, m, field, self.store.settings(), self.camera.status,
                                      is_local, self.telemetry_enabled, real=not self.conn.sim)
        blockers = preflight.blocking(checks)
        out = {"mission_id": mid, "checks": checks, "ready": not blockers and not self.active_id,
               "blocking": [c["id"] for c in blockers], "sim": self.conn.sim}
        if self.active_id:
            out["blocking"].append("active_mission")
        if out["ready"]:
            out["token"], out["token_ttl_s"] = self.tokens.issue(mid)
        return out

    # ---------------------------------------------------------------- start
    def launch(self, mid, token, is_local, actor):
        with self.lock:
            err = self.tokens.redeem(token, mid)
            if err:
                self.store.event(mid, "warn", "command", f"Start refused: {err}")
                raise MissionError(err, 403)
            if self.active_id:
                raise MissionError("Another mission is in progress.")
            pf = self.preflight(mid, is_local)   # re-checked server-side at the moment of launch
            if pf["blocking"]:
                names = ", ".join(c["label"] for c in pf["checks"] if c["id"] in pf["blocking"])
                self.store.event(mid, "warn", "command", f"Start refused, checks failing: {names}")
                raise MissionError(f"Checks no longer pass: {names}.", 409, pf)
            m = self._get(mid)
            self.store.event(mid, "info", "command", f"Launch confirmed by {actor}.")
            self.store.update_mission(mid, status="starting", started=now())
            self.active_id = mid
            self._reset_live(m)
            for step, fn in (("mode GUIDED", lambda: self.link.set_mode("GUIDED")),
                             ("arm", self.link.arm),
                             ("mode AUTO", lambda: self.link.set_mode("AUTO")),
                             ("mission start", self.link.mission_start)):
                res = fn()
                self._log_cmd(mid, step, res)
                if not res["ok"]:
                    self._start_failed(mid, step, res)
                    raise MissionError(f"Start stopped at '{step}': {res['detail']}", 502, res)
            self.store.event(mid, "info", "stage", "Mission started; climbing to survey altitude.")
            return self._get(mid)

    def _start_failed(self, mid, step, res):
        s = self.link.snapshot()
        note = ""
        if s.get("armed") and (s.get("alt_rel") or 0) < 1.0:
            dis = self.link.command(400, (0, 0), label="disarm")   # normal (not forced) disarm on the ground
            self._log_cmd(mid, "disarm after failed start", dis)
            note = " Disarm " + ("confirmed." if dis["ok"] else f"not acknowledged ({dis['detail']}).")
        elif s.get("armed"):
            note = " The drone is armed and off the ground: use Return to launch or take over with the transmitter."
        self.store.event(mid, "error", "stage", f"Start failed at '{step}': {res['detail']}.{note}")
        self.store.update_mission(mid, status="failed", ended=now(), end_reason=f"{step}: {res['detail']}{note}")
        self.active_id = None if not s.get("armed") or (s.get("alt_rel") or 0) < 1.0 else mid

    # ---------------------------------------------------------------- in-flight commands
    def flight_command(self, action, actor):
        s = self.link.snapshot()
        mid = self.active_id
        if not s.get("link"):
            raise MissionError("No link to the flight controller.", 503)
        if not s.get("armed"):
            raise MissionError("The drone is not armed; there is nothing to " + action + ".")
        if action == "pause":
            res = self.link.set_mode("BRAKE")
            if not res["ok"]:
                self._log_cmd(mid, "pause (BRAKE)", res)
                res = self.link.set_mode("LOITER")
                step = "pause (LOITER fallback)"
            else:
                step = "pause (BRAKE)"
        elif action == "resume":
            if s.get("mode") not in ("BRAKE", "LOITER"):
                raise MissionError(f"Resume only works from a pause (mode is {s.get('mode')}).")
            res, step = self.link.set_mode("AUTO"), "resume (AUTO)"
        elif action == "rtl":
            res, step = self.link.set_mode("RTL"), "return to launch"
        elif action == "land":
            res, step = self.link.set_mode("LAND"), "land now"
        else:
            raise MissionError("Unknown command.", 400)
        self._log_cmd(mid, f"{step} by {actor}", res)
        if mid and res["ok"] and action == "pause":
            self.live["paused"] = True
        if mid and res["ok"] and action == "resume":
            self.live["paused"] = False
        return res

    def _log_cmd(self, mid, step, res):
        entry = {"t": now(), "mission_id": mid, "command": step, **res}
        self.commands.insert(0, entry)
        del self.commands[30:]
        level = "info" if res.get("ok") else "error"
        self.store.event(mid, level, "ack", f"{step}: {res.get('stage')} - {res.get('detail')}", res)

    # ---------------------------------------------------------------- monitor
    def _reset_live(self, m):
        self.live = {"mission_id": m["id"], "max_seq": 0, "airborne": False, "paused": False,
                     "start_v": None, "max_alt": 0.0, "dist_m": 0.0, "last_pos": None, "last_capture_pos": None,
                     "captures": 0, "skipped_captures": 0, "last_detection": None, "stage_since": now(),
                     "link_lost_t": None}
        self.track_buf = []

    def run(self):
        last_track, last_flush = 0.0, now()
        while True:
            time.sleep(0.5)
            try:
                mid = self.active_id
                if not mid:
                    continue
                m = self.store.mission(mid)
                s = self.link.snapshot()
                self._monitor(m, s)
                if s.get("link") and s.get("armed") and now() - last_track >= 1 and s.get("lat") is not None:
                    self.track_buf.append((mid, now(), s["lat"], s["lon"], s.get("alt_rel"),
                                           s.get("groundspeed"), s.get("heading")))
                    last_track = now()
                if self.track_buf and (now() - last_flush > 5 or not self.active_id):
                    self.store.add_track(self.track_buf)
                    self.track_buf, last_flush = [], now()
                    if self.active_id:   # survives a console restart mid-flight
                        live = self.live
                        self.store.update_mission(self.active_id, stats={"checkpoint": {
                            k: live.get(k) for k in ("max_seq", "max_frac", "start_v", "airborne", "max_alt")}})
            except Exception as exc:  # never let the monitor die mid-flight
                self.store.event(self.active_id, "error", "server", f"Monitor error: {exc}")
                time.sleep(2)

    def _set_stage(self, m, stage, text=None, level="info"):
        if m["status"] != stage:
            self.store.update_mission(m["id"], status=stage)
            self.store.event(m["id"], level, "stage", text or STAGE_TEXT.get(stage, stage))
            self.live["stage_since"] = now()
            m["status"] = stage

    def _monitor(self, m, s):
        live, plan = self.live, m["plan"]
        if not s.get("link"):
            if not live.get("link_lost_t"):
                live["link_lost_t"] = now()
                self.store.event(m["id"], "error", "link",
                                 "Telemetry lost. The autopilot continues its mission and failsafes on its own.")
            return
        if live.get("link_lost_t"):
            self.store.event(m["id"], "info", "link",
                             f"Telemetry restored after {now() - live['link_lost_t']:.0f} s.")
            live["link_lost_t"] = None
        armed, mode, alt = s.get("armed"), s.get("mode"), s.get("alt_rel") or 0.0
        seq = s.get("mission_seq")
        if armed and live["start_v"] is None:
            live["start_v"] = s.get("battery_v")
        if armed and alt > 1.0:
            live["airborne"] = True
        live["max_alt"] = max(live["max_alt"], alt)
        pos = (s["lat"], s["lon"]) if s.get("lat") is not None else None
        if pos and armed and live["last_pos"]:
            step = distance_m(live["last_pos"], pos)
            if step < 50:   # ignore GPS jumps
                live["dist_m"] += step
        live["last_pos"] = pos if armed else live["last_pos"]
        if mode == "AUTO" and seq is not None:
            live["max_seq"] = max(live["max_seq"], seq)

        if not armed:
            if live["airborne"] or now() - (m["started"] or now()) > 20:
                self._finish(m, s)
            return
        if mode == "AUTO":
            live["paused"] = False
            if seq is None or seq < plan["first_wp_seq"]:
                self._set_stage(m, "takeoff")
            elif seq <= plan["last_wp_seq"]:
                self._set_stage(m, "surveying")
                self._maybe_capture(m, s)
            else:
                self._set_stage(m, "returning")
        elif mode == "RTL":
            self._set_stage(m, "returning")
        elif mode == "LAND":
            self._set_stage(m, "landing")
        elif mode in ("BRAKE", "LOITER") and live.get("paused"):
            self._set_stage(m, "paused")
        elif mode not in ("GUIDED",):
            self._set_stage(m, "pilot", f"Mode changed to {mode} outside the console: the pilot has control.",
                            "warn")

    def _finish(self, m, s):
        live, plan = self.live, m["plan"]
        if self.track_buf:
            self.store.add_track(self.track_buf)
            self.track_buf = []
        done = live["max_seq"] >= plan["last_wp_seq"]
        status = "completed" if done else "aborted"
        reason = None if done else (
            "Landed before finishing the survey." if live["airborne"] else "Disarmed before take-off.")
        track = self.store.track(m["id"])
        dist = sum(d for d in (distance_m((a["lat"], a["lon"]), (b["lat"], b["lon"]))
                               for a, b in zip(track, track[1:]) if a["lat"] is not None and b["lat"] is not None)
                   if d < 50)
        captures = self.store.one("SELECT COUNT(*) AS n FROM detections WHERE mission_id=?", (m["id"],))["n"]
        stats = {"duration_s": round(now() - (m["started"] or now())), "distance_m": round(max(dist, live["dist_m"])),
                 "max_alt_m": round(max([live["max_alt"]] + [t["alt"] or 0 for t in track]), 1), "captures": captures,
                 "skipped_captures": live["skipped_captures"],
                 "survey_progress": round(max(live.get("max_frac", 0.0), route_progress(plan, live["max_seq"])) * 100),
                 "battery_start_v": live["start_v"], "battery_end_v": s.get("battery_v")}
        self.store.update_mission(m["id"], status=status, ended=now(), stats=stats, end_reason=reason)
        self.store.event(m["id"], "info" if done else "warn", "stage",
                         "Mission completed and landed." if done else f"Mission ended early: {reason}")
        self.active_id = None

    # ---------------------------------------------------------------- inspection captures
    def _maybe_capture(self, m, s):
        if s.get("lat") is None or self.capture_busy:
            return
        pos, last = (s["lat"], s["lon"]), self.live["last_capture_pos"]
        if last and distance_m(last, pos) < m["plan"]["capture_spacing_m"]:
            return
        self.live["last_capture_pos"] = pos
        if self.camera.frame is None or now() - self.camera.frame_time > 2:
            self.live["skipped_captures"] += 1
            return
        self.capture_busy = True
        threading.Thread(target=self._capture, args=(m, dict(s), self.camera.frame), daemon=True).start()

    def _capture(self, m, s, frame):
        try:
            d = analyse_and_store(self.store, self.analyzer, frame, s, source="mission",
                                  mission_id=m["id"], field_id=m["field_id"])
            self.live["captures"] += 1
            self.live["last_detection"] = {k: d[k] for k in ("id", "t", "label", "kind", "p", "status",
                                                             "lat", "lon", "thumb", "n_leaves")}
        except Exception as exc:
            self.store.event(m["id"], "error", "capture", f"Capture failed: {exc}")
        finally:
            self.capture_busy = False

    # ---------------------------------------------------------------- SSE payload
    def status(self):
        mid = self.active_id
        out = {"active": None, "commands": self.commands[:8]}
        if not mid:
            return out
        m = self.store.mission(mid)
        if not m:
            return out
        s = self.link.snapshot()
        plan, live = m["plan"], self.live
        pos = (s["lat"], s["lon"]) if s.get("lat") is not None else None
        seq = s.get("mission_seq") if s.get("mode") == "AUTO" or m["status"] == "paused" else live.get("max_seq")
        frac = route_progress(plan, seq, pos) if m["status"] in ("surveying", "paused") else 0.0
        live["max_frac"] = frac = max(frac, live.get("max_frac", 0.0))   # never goes backwards (e.g. while returning)
        remaining = plan["survey_length_m"] * (1 - frac)
        speed = plan["params"]["speed_ms"]
        eta = None
        if m["status"] in ("takeoff", "surveying", "paused"):
            eta = remaining / speed + (plan["total_length_m"] - plan["survey_length_m"]) / speed \
                + plan["params"]["altitude_m"] / 1.5
        out["active"] = {
            "id": mid, "field_id": m["field_id"], "status": m["status"], "stage_text": STAGE_TEXT.get(m["status"]),
            "started": m["started"], "elapsed_s": round(now() - m["started"]) if m["started"] else None,
            "progress_pct": round(frac * 100, 1), "seq": seq, "first_wp_seq": plan["first_wp_seq"],
            "last_wp_seq": plan["last_wp_seq"], "eta_s": round(eta) if eta is not None else None,
            "captures": live.get("captures", 0), "skipped_captures": live.get("skipped_captures", 0),
            "last_detection": live.get("last_detection"), "distance_m": round(live.get("dist_m", 0)),
            "link_lost": bool(live.get("link_lost_t")), "sim": m["sim"],
        }
        return out


# ---------------------------------------------------------------- shared capture path
def analyse_and_store(store, analyzer, frame, s, source, mission_id=None, field_id=None, extra=None):
    """Run the model on a frame, save the images, and store one detection with its GPS context.

    Position is only attached when telemetry is fresh (< 1 s) and the fix is 3D; otherwise the
    detection is kept but not placed on the map."""
    result = analyzer.predict(frame)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")[:-3]
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    full, thumb = f"{stamp}.jpg", f"{stamp}_thumb.jpg"
    cv2.imwrite(str(CAPTURE_DIR / full), frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
    cv2.imwrite(str(CAPTURE_DIR / thumb), cv2.resize(frame, (320, 180)), [cv2.IMWRITE_JPEG_QUALITY, 80])
    age = s.get("position_age_s")
    fresh = s.get("link") and age is not None and age <= TELEMETRY_MAX_AGE_S
    fixed = (s.get("gps_fix") or 0) >= 3
    mapped = bool(fresh and fixed)
    lat, lon = (s.get("lat"), s.get("lon")) if mapped else (None, None)
    if mapped and not field_id:
        field_id = field_at(store, lat, lon)
    h_acc = s.get("gps_h_acc_m") or (round(s["gps_hdop"] * 2.5, 1) if s.get("gps_hdop") else None)
    head = result["headline"]
    d = {
        "id": f"D{stamp}", "t": now(), "source": source, "mission_id": mission_id, "field_id": field_id,
        "lat": lat, "lon": lon, "alt": s.get("alt_rel") if mapped else None, "gps_fix": s.get("gps_fix"),
        "hdop": s.get("gps_hdop"), "h_acc_m": h_acc if mapped else None,
        "telemetry_age_ms": round(age * 1000) if age is not None else None,
        "label": head["label"], "p": head["p"], "status": head.get("status"), "top": head.get("top"),
        "leaves": [{k: v for k, v in l.items() if k != "probs"} for l in result["leaves"]],
        "image": full, "thumb": thumb, "model": result.get("model"), "mode": s.get("mode"),
        **(extra or {}),
    }
    return store.add_detection(d)


def field_at(store, lat, lon):
    for f in store.fields():
        frame = LocalFrame(lat, lon)
        xy = [frame.to_xy(*p) for p in f["polygon"]]
        if point_in_polygon(0.0, 0.0, xy):
            return f["id"]
    return None
