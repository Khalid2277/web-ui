"""Survey route planner: field polygon -> boustrophedon ("lawn-mower") inspection mission.

Pure functions, no I/O. Lines run parallel to the field's longest edge (fewest turns), spaced
by the camera swath minus side-lap. Output includes ArduPilot mission items and honest
estimates whose assumptions (camera field of view, endurance) are returned alongside.
"""
import math

from geo import LocalFrame, centroid, shoelace

# MAVLink constants (kept local so this module stays importable without pymavlink).
MAV_FRAME_GLOBAL_RELATIVE_ALT_INT = 6
MAV_CMD_NAV_WAYPOINT = 16
MAV_CMD_NAV_RETURN_TO_LAUNCH = 20
MAV_CMD_NAV_TAKEOFF = 22
MAV_CMD_DO_CHANGE_SPEED = 178

CLIMB_MS, DESCEND_MS = 2.5, 1.5   # ArduCopter defaults (PILOT_SPD_UP / LAND_SPD_MS order of magnitude)
TURN_PENALTY_S = 3.0              # deceleration + acceleration at each survey-line end
MAX_WAYPOINTS = 250


class PlanError(ValueError):
    pass


def swath_m(altitude_m, hfov_deg):
    """Ground width imaged by the camera at a given altitude (nadir camera)."""
    return 2 * altitude_m * math.tan(math.radians(hfov_deg) / 2)


def _rotate(x, y, a):
    c, s = math.cos(a), math.sin(a)
    return x * c - y * s, x * s + y * c


def _longest_edge_angle(xy):
    best, ang = -1.0, 0.0
    for (x1, y1), (x2, y2) in zip(xy, xy[1:] + xy[:1]):
        d = math.hypot(x2 - x1, y2 - y1)
        if d > best:
            best, ang = d, math.atan2(y2 - y1, x2 - x1)
    return ang


def _scanline(y, xy):
    """x-intervals where the horizontal line at y lies inside the polygon."""
    xs = []
    for (x1, y1), (x2, y2) in zip(xy, xy[1:] + xy[:1]):
        if (y1 > y) != (y2 > y):
            xs.append(x1 + (y - y1) * (x2 - x1) / (y2 - y1))
    xs.sort()
    return [(xs[i], xs[i + 1]) for i in range(0, len(xs) - 1, 2)]


def plan_survey(polygon, altitude_m=12.0, speed_ms=3.0, hfov_deg=70.0, sidelap=0.25,
                endurance_min=8.0, edge_margin_m=1.0):
    """Plan an inspection of `polygon` ([[lat, lon], ...]). Raises PlanError on bad input."""
    if not 3 <= altitude_m <= 60:
        raise PlanError("Altitude must be between 3 and 60 m.")
    if not 1 <= speed_ms <= 10:
        raise PlanError("Speed must be between 1 and 10 m/s.")
    if not 0 <= sidelap < 0.9:
        raise PlanError("Side-lap must be between 0 and 0.9.")
    pts = [(float(p[0]), float(p[1])) for p in polygon]
    lat0, lon0 = centroid(pts)
    frame = LocalFrame(lat0, lon0)
    xy = [frame.to_xy(*p) for p in pts]
    if shoelace(xy) < 0:  # orientation does not matter for scanlines, but keep it consistent
        xy = xy[::-1]
    ang = _longest_edge_angle(xy)
    rot = [_rotate(x, y, -ang) for x, y in xy]           # survey lines become horizontal
    swath = swath_m(altitude_m, hfov_deg)
    spacing = swath * (1 - sidelap)
    ys = [y for _, y in rot]
    y, y_max = min(ys) + spacing / 2, max(ys)
    lines = []
    while y < y_max:
        for x0, x1 in _scanline(y, rot):
            x0, x1 = x0 + edge_margin_m, x1 - edge_margin_m
            if x1 - x0 >= 1.0:
                lines.append((y, x0, x1))
        y += spacing
    if not lines:
        # Field narrower than one swath: a single pass along its middle.
        mid = (min(ys) + y_max) / 2
        lines = [(mid, a + edge_margin_m, b - edge_margin_m) for a, b in _scanline(mid, rot)]
    if not lines:
        raise PlanError("The field is too narrow to plan a survey line.")
    path_rot = []
    for i, (yy, x0, x1) in enumerate(lines):
        ends = [(x0, yy), (x1, yy)]
        path_rot.extend(ends if i % 2 == 0 else ends[::-1])
    if len(path_rot) > MAX_WAYPOINTS:
        raise PlanError(f"The route needs {len(path_rot)} waypoints (max {MAX_WAYPOINTS}). "
                        "Fly higher or split the field.")
    path_xy = [_rotate(x, y, ang) for x, y in path_rot]
    waypoints = [frame.to_latlon(x, y) for x, y in path_xy]

    survey_len = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(path_xy, path_xy[1:]))
    # Transit from launch (assumed near the first waypoint) and home again from the last one.
    return_len = math.hypot(path_xy[-1][0] - path_xy[0][0], path_xy[-1][1] - path_xy[0][1])
    total_len = survey_len + return_len
    duration_s = (total_len / speed_ms + altitude_m / CLIMB_MS + altitude_m / DESCEND_MS
                  + TURN_PENALTY_S * len(lines) + 10)
    battery_pct = 100 * duration_s / (endurance_min * 60) if endurance_min > 0 else None

    items = [  # seq 0 is the home slot; ArduPilot overwrites it with the real home position
        _item(0, MAV_CMD_NAV_WAYPOINT, waypoints[0][0], waypoints[0][1], 0),
        _item(1, MAV_CMD_NAV_TAKEOFF, 0, 0, altitude_m),
        _item(2, MAV_CMD_DO_CHANGE_SPEED, 0, 0, 0, p1=1, p2=speed_ms, p3=-1),
    ]
    first_wp_seq = len(items)
    for la, lo in waypoints:
        items.append(_item(len(items), MAV_CMD_NAV_WAYPOINT, la, lo, altitude_m))
    last_wp_seq = len(items) - 1
    items.append(_item(len(items), MAV_CMD_NAV_RETURN_TO_LAUNCH, 0, 0, 0))

    return {
        "params": {"altitude_m": altitude_m, "speed_ms": speed_ms, "hfov_deg": hfov_deg,
                   "sidelap": sidelap, "endurance_min": endurance_min},
        "swath_m": round(swath, 2), "line_spacing_m": round(spacing, 2), "lines": len(lines),
        "waypoints": [[round(a, 7), round(b, 7)] for a, b in waypoints],
        "survey_length_m": round(survey_len, 1), "total_length_m": round(total_len, 1),
        "est_duration_s": round(duration_s), "est_battery_pct": round(battery_pct, 1) if battery_pct else None,
        # Capture roughly every swath length along the track: ~25 % forward overlap at 4:3 aspect.
        "capture_spacing_m": round(max(2.0, swath * 0.75 * 0.75), 1),
        "items": items, "first_wp_seq": first_wp_seq, "last_wp_seq": last_wp_seq,
        "assumptions": [
            f"Camera horizontal field of view {hfov_deg:g}° (setting) gives a {swath:.1f} m swath at {altitude_m:g} m.",
            f"Battery estimate uses a {endurance_min:g}-minute usable flight time (setting).",
            "Transit assumes launch near the first waypoint.",
        ],
    }


def _item(seq, cmd, lat, lon, alt, p1=0.0, p2=0.0, p3=0.0, p4=0.0):
    return {"seq": seq, "frame": MAV_FRAME_GLOBAL_RELATIVE_ALT_INT, "command": cmd,
            "param1": p1, "param2": p2, "param3": p3, "param4": p4,
            "x": int(round(lat * 1e7)), "y": int(round(lon * 1e7)), "z": float(alt)}


def route_progress(plan, seq, position=None):
    """Fraction of the survey flown, from the current mission item (and position within the leg)."""
    first, last = plan["first_wp_seq"], plan["last_wp_seq"]
    if seq is None or seq < first:
        return 0.0
    if seq > last:
        return 1.0
    n = last - first  # number of legs between survey waypoints
    if n <= 0:
        return 1.0
    k = seq - first  # index of the waypoint being flown to; the current leg is k-1 -> k
    if k == 0:
        return 0.0   # still in transit to the first survey waypoint
    frac = 0.0
    if position:
        wps = plan["waypoints"]
        frame = LocalFrame(*wps[k - 1])
        bx, by = frame.to_xy(*wps[k])
        px, py = frame.to_xy(*position)
        leg = math.hypot(bx, by)
        if leg > 0.5:
            frac = max(0.0, min(1.0, (px * bx + py * by) / (leg * leg)))
    return max(0.0, min(1.0, (k - 1 + frac) / n))
