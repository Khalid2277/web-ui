"""Pre-launch checks. A mission can only start when every critical check passes.

Each check: {"id", "label", "status": "pass"|"fail"|"warn", "detail", "critical": bool}.
Checks read live telemetry (never cached guesses); missing data is a failure, not a pass.
"""
import secrets
import threading

from config import now
from geo import centroid, distance_m

TOKEN_TTL_S = 60
HOME_FAR_WARN_M, HOME_FAR_FAIL_M = 200, 1000
BATTERY_RESERVE_PCT = 25          # the planned flight may use at most 75 % of the endurance setting

_param_cache = {}                  # name -> (t, value)
_param_lock = threading.Lock()


def clear_cache():
    with _param_lock:
        _param_cache.clear()


def cached_param(link, name, ttl=30):
    with _param_lock:
        hit = _param_cache.get(name)
        if hit and now() - hit[0] < ttl:
            return hit[1]
    value = link.param(name)
    if value is not None:
        with _param_lock:
            _param_cache[name] = (now(), value)
    return value


def _check(cid, label, ok, detail, critical=True, warn=False):
    status = "pass" if ok else ("warn" if warn or not critical else "fail")
    return {"id": cid, "label": label, "status": status, "detail": detail, "critical": critical}


def run_checks(link, mission, field, settings, camera_status, is_local, telemetry_enabled=True, real=False):
    s = link.snapshot() if link else {}
    checks = []
    add = checks.append

    add(_check("network", "Command from the drone's local network", is_local,
               "Request is from the local network." if is_local
               else "Commands are disabled over the public link. Connect to the drone's Wi-Fi."))
    add(_check("field", "Field selected", bool(field),
               f"{field['name']}" if field else "The mission has no field."))
    linked = bool(link) and telemetry_enabled and s.get("link")
    add(_check("link", "Flight controller connected", linked,
               f"Heartbeat {s.get('heartbeat_age_s')} s ago." if linked
               else "No heartbeat from the flight controller in the last 3 s."))

    def tel(cid, label, ok, good, bad, **kw):
        add(_check(cid, label, bool(linked and ok), good if linked and ok else (bad if linked else "No telemetry."), **kw))

    tel("disarmed", "Drone is disarmed", s.get("armed") is False, "Disarmed and on the ground.",
        "The drone is already armed. Disarm it before starting a mission.")
    tel("prearm", "Autopilot pre-arm checks", s.get("prearm_ok") is True, "Autopilot reports pre-arm OK.",
        "The autopilot's own pre-arm checks are failing; see its messages in the log.")
    fix, sats, hdop = s.get("gps_fix") or 0, s.get("gps_sats") or 0, s.get("gps_hdop")
    gps_ok = fix >= 3 and sats >= settings["min_sats"] and hdop is not None and hdop <= settings["max_hdop"]
    tel("gps", "GPS 3D fix and accuracy", gps_ok,
        f"Fix type {fix}, {sats} satellites, HDOP {hdop}.",
        f"Fix type {fix}, {sats} satellites, HDOP {hdop}. Need 3D fix, at least {settings['min_sats']:g} "
        f"satellites and HDOP at most {settings['max_hdop']:g}.")
    tel("ekf", "Position estimate (EKF)", s.get("ekf_pos_ok") is True, "EKF reports a valid absolute position.",
        "EKF has no absolute horizontal position yet." if s.get("ekf_t") else "No EKF status received yet.")
    home = s.get("home")
    tel("home", "Home position set", bool(home), "Home recorded by the autopilot.",
        "The autopilot has not set a home position (needs GPS lock).")

    plan = (mission or {}).get("plan") or {}
    if linked and home and plan.get("waypoints"):
        d = distance_m((home["lat"], home["lon"]), plan["waypoints"][0])
        detail = f"Home is {d:.0f} m from the first waypoint."
        if d > HOME_FAR_WARN_M:
            detail += " The time and battery estimates assume launching at the field."
        add({"id": "home_near", "label": "Launch point near the field", "critical": True, "detail": detail,
             "status": "pass" if d <= HOME_FAR_WARN_M else "warn" if d <= HOME_FAR_FAIL_M else "fail"})

    v = s.get("battery_v")
    tel("battery", "Battery voltage", v is not None and v >= settings["min_battery_v"], f"{v} V.",
        f"{v} V is below the {settings['min_battery_v']:g} V minimum. Fit a charged pack.")
    est = plan.get("est_battery_pct")
    limit = 100 - BATTERY_RESERVE_PCT
    add(_check("endurance", "Flight fits the battery", est is not None and est <= limit,
               f"Estimated {est:.0f} % of the {settings['endurance_min']:g}-minute endurance (limit {limit} %)."
               if est is not None else "No battery estimate in the plan."))
    rc_fresh = s.get("rc_ok") and s.get("rc_t") and now() - s["rc_t"] < 3
    tel("rc", "RC transmitter linked (pilot can take over)", rc_fresh, "RC signal present.",
        "No RC signal. A pilot with a transmitter must be able to take over.")
    status = (mission or {}).get("status")
    verified = bool(plan.get("verified")) and status in ("uploaded", "ready")
    add(_check("mission", "Mission uploaded and verified", verified,
               "Read back from the autopilot and matches the plan." if verified
               else "Upload the mission to the drone (it is verified by reading it back)."))
    # Same plan still on the autopilot? (someone may have uploaded another mission with a GCS since)
    total = s.get("mission_total")
    if verified and linked and total is not None:
        add(_check("mission_count", "Autopilot holds this mission", total == len(plan["items"]) - 1 or
                   total == len(plan["items"]),
                   f"Autopilot reports {total} mission items.",))

    # ---- autopilot safety configuration. On the real drone these block launch; in the simulator
    # they are advisory (SITL defaults differ from the airframe and nothing real can be hurt).
    if linked:
        def safety(cid, label, param, ok_fn, good, bad):
            v = cached_param(link, param)
            ok = v is not None and ok_fn(v)
            detail = good if ok else (bad if v is not None else f"Could not read {param} from the autopilot.")
            add(_check(cid, label, ok, detail, critical=real))
        safety("arming_checks", "Autopilot arming checks enabled", "ARMING_CHECK", lambda v: v != 0,
               "ARMING_CHECK is on, so the autopilot runs its own safety checks before arming.",
               "ARMING_CHECK=0: the autopilot skips its own safety checks. Set ARMING_CHECK=1 in Mission Planner.")
        safety("rc_failsafe", "RC-loss failsafe enabled", "FS_THR_ENABLE", lambda v: v != 0,
               "If the transmitter signal is lost, the drone returns or lands on its own.",
               "FS_THR_ENABLE=0: nothing happens if the transmitter signal is lost. Set FS_THR_ENABLE=1 (RTL).")
        safety("batt_failsafe", "Low-battery failsafe enabled", "BATT_FS_LOW_ACT", lambda v: v != 0,
               "On low battery the drone returns or lands on its own.",
               "BATT_FS_LOW_ACT=0: no action on low battery. Set BATT_FS_LOW_ACT=2 (RTL) and BATT_LOW_VOLT (e.g. 10.5 V for 3S).")
        safety("fence", "Geofence enabled", "FENCE_ENABLE", lambda v: v == 1,
               "Geofence is on: the drone cannot fly beyond its limits.",
               "Geofence is off (FENCE_ENABLE=0). Set FENCE_ENABLE=1 with FENCE_RADIUS and FENCE_ALT_MAX.")
        rtl_m = cached_param(link, "RTL_ALT_M")              # Copter 4.7+: metres
        if rtl_m is None:
            cm = cached_param(link, "RTL_ALT")                # older firmware: centimetres
            rtl_m = cm / 100 if cm is not None else None
        add(_check("rtl_alt", "Return-to-launch altitude", rtl_m is not None and rtl_m >= 10,
                   (f"Returns at {rtl_m:.0f} m." if rtl_m >= 10 else f"Returns at only {rtl_m:.0f} m; 10 m or more "
                    "clears trees and buildings.") if rtl_m is not None else "Could not read the RTL altitude parameter.",
                   critical=False))
    cam_ok = camera_status == "live"
    add(_check("camera", "Camera", cam_ok, "Camera live; captures will be analysed." if cam_ok
               else f"Camera {camera_status}. The drone can fly, but no inspection images will be taken.",
               critical=False))
    return checks


def blocking(checks):
    return [c for c in checks if c["critical"] and c["status"] == "fail"]


class Tokens:
    """Single-use launch tokens, valid for TOKEN_TTL_S, bound to one mission."""

    def __init__(self):
        self.items = {}
        self.lock = threading.Lock()

    def issue(self, mission_id):
        tok = secrets.token_urlsafe(16)
        with self.lock:
            self.items = {k: v for k, v in self.items.items() if v[1] > now()}
            self.items[tok] = (mission_id, now() + TOKEN_TTL_S)
        return tok, TOKEN_TTL_S

    def redeem(self, tok, mission_id):
        with self.lock:
            item = self.items.pop(tok or "", None)
        if not item:
            return "The launch confirmation is missing or was already used. Run the checks again."
        if item[0] != mission_id:
            return "That confirmation belongs to a different mission."
        if item[1] < now():
            return "The launch confirmation expired (60 s). Run the checks again."
        return None
