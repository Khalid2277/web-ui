"""Unit tests for the farm command center's pure logic (no camera, no drone).

    python3 -m pytest tests/test_dashboard.py -q
"""
import math
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "raspberry_pi" / "dashboard"))

import analytics  # noqa: E402
import preflight  # noqa: E402
from geo import LocalFrame, area_m2, validate_polygon  # noqa: E402
from mavlink_link import mission_matches  # noqa: E402
from planner import PlanError, plan_survey, route_progress, swath_m  # noqa: E402
from store import Store, kind_of, leaf_counts  # noqa: E402

HOME = (25.3094, 55.4861)


def rect(w, h, lat=HOME[0], lon=HOME[1]):
    f = LocalFrame(lat, lon)
    return [list(f.to_latlon(x, y)) for x, y in ((0, 0), (w, 0), (w, h), (0, h))]


# ---------------------------------------------------------------- geo
def test_area_of_rectangle():
    assert area_m2(rect(100, 50)) == pytest.approx(5000, rel=1e-3)


@pytest.mark.parametrize("poly,msg", [
    ([[0, 0], [1, 1]], "at least 3"),
    ([[0, 0], [0, 1e-6], [1e-6, 0]], "smaller than 25"),
    ([[95, 0], [0, 1], [1, 0]], "outside valid"),
])
def test_polygon_rejected(poly, msg):
    assert msg in validate_polygon(poly)


def test_bow_tie_rejected():
    r = rect(50, 50)
    assert "crosses itself" in validate_polygon([r[0], r[2], r[1], r[3]])


# ---------------------------------------------------------------- planner
def test_plan_structure_and_spacing():
    p = plan_survey(rect(80, 60), altitude_m=10, hfov_deg=70, sidelap=0.25)
    assert p["swath_m"] == pytest.approx(swath_m(10, 70), abs=0.01)
    assert p["line_spacing_m"] == pytest.approx(p["swath_m"] * 0.75, abs=0.01)
    cmds = [i["command"] for i in p["items"]]
    assert cmds[1] == 22 and cmds[2] == 178 and cmds[-1] == 20          # takeoff, speed ... RTL last
    assert all(c == 16 for c in cmds[p["first_wp_seq"]:p["last_wp_seq"] + 1])
    assert [i["seq"] for i in p["items"]] == list(range(len(p["items"])))
    # lines run along the longest (80 m) edge
    assert p["lines"] == math.ceil(60 / p["line_spacing_m"]) or p["lines"] == math.floor(60 / p["line_spacing_m"]) + 1


def test_waypoints_inside_field():
    poly = [[25.3100, 55.4860], [25.3104, 55.4870], [25.3096, 55.4874], [25.3093, 55.4866]]   # irregular
    p = plan_survey(poly, altitude_m=8)
    f = LocalFrame(*p["waypoints"][0])
    from geo import point_in_polygon
    xy = [f.to_xy(*q) for q in poly]
    for la, lo in p["waypoints"]:
        x, y = f.to_xy(la, lo)
        # waypoints sit 1 m inside the edge; allow tiny numeric slack
        assert point_in_polygon(x, y, xy) or min(math.hypot(x - a, y - b) for a, b in xy) < 1.5


def test_concave_field_splits_lines():
    f = LocalFrame(*HOME)
    u = [(0, 0), (80, 0), (80, 60), (55, 60), (55, 20), (25, 20), (25, 60), (0, 60)]   # U, longest edge at the bottom
    p = plan_survey([list(f.to_latlon(x, y)) for x, y in u], altitude_m=5)
    assert p["lines"] > math.ceil(60 / p["line_spacing_m"])   # upper rows are split in two


def test_plan_limits():
    with pytest.raises(PlanError):
        plan_survey(rect(50, 50), altitude_m=100)
    with pytest.raises(PlanError):
        plan_survey(rect(3000, 3000), altitude_m=3)   # too many waypoints


def test_battery_estimate_scales_with_endurance():
    a = plan_survey(rect(80, 60), endurance_min=8)
    b = plan_survey(rect(80, 60), endurance_min=16)
    assert a["est_battery_pct"] == pytest.approx(2 * b["est_battery_pct"], rel=0.02)


def test_route_progress():
    p = plan_survey(rect(80, 60), altitude_m=10)
    first, last = p["first_wp_seq"], p["last_wp_seq"]
    assert route_progress(p, None) == 0
    assert route_progress(p, first) == 0
    assert route_progress(p, last + 1) == 1
    mid = route_progress(p, first + 1, p["waypoints"][0])
    assert mid == 0
    assert route_progress(p, first + 1, p["waypoints"][1]) == pytest.approx(1 / (last - first), abs=1e-6)


def test_mission_matches():
    p = plan_survey(rect(80, 60))
    back = [{"seq": i["seq"], "command": i["command"], "x": i["x"], "y": i["y"], "z": i["z"]} for i in p["items"]]
    assert mission_matches(p["items"], back)[0]
    back[4]["x"] += 100
    assert not mission_matches(p["items"], back)[0]
    assert not mission_matches(p["items"], back[:-1])[0]
    assert not mission_matches(p["items"], None)[0]


# ---------------------------------------------------------------- preflight
class FakeLink:
    def __init__(self, **over):
        self.s = {"link": True, "heartbeat_age_s": 0.4, "armed": False, "prearm_ok": True, "gps_fix": 3,
                  "gps_sats": 12, "gps_hdop": 0.8, "ekf_pos_ok": True, "ekf_t": time.time(),
                  "home": {"lat": HOME[0], "lon": HOME[1]}, "battery_v": 12.5, "rc_ok": True, "rc_t": time.time()}
        self.s.update(over)

    def snapshot(self):
        return dict(self.s)

    def param(self, name):
        return {"FENCE_ENABLE": 1, "RTL_ALT_M": 15}.get(name)


SETTINGS = {"min_sats": 8, "max_hdop": 2.0, "min_battery_v": 11.4, "endurance_min": 8}


def mission(**over):
    p = plan_survey(rect(40, 30, HOME[0] + 0.0002, HOME[1]))
    p["verified"] = True
    return {"status": "uploaded", "plan": p, **over}


def run(link=None, m=None, local=True, cam="live"):
    preflight._param_cache.clear()
    return preflight.run_checks(link or FakeLink(), m or mission(), {"name": "A"}, SETTINGS, cam, local)


def test_preflight_all_pass():
    assert preflight.blocking(run()) == []


@pytest.mark.parametrize("over,check", [
    ({"link": False}, "link"), ({"armed": True}, "disarmed"), ({"prearm_ok": False}, "prearm"),
    ({"gps_fix": 2}, "gps"), ({"gps_sats": 5}, "gps"), ({"gps_hdop": 3.1}, "gps"), ({"gps_hdop": None}, "gps"),
    ({"ekf_pos_ok": False}, "ekf"), ({"home": None}, "home"), ({"battery_v": 10.9}, "battery"),
    ({"battery_v": None}, "battery"), ({"rc_ok": False}, "rc"), ({"rc_t": time.time() - 10}, "rc"),
])
def test_each_critical_check_blocks(over, check):
    blocked = [c["id"] for c in preflight.blocking(run(FakeLink(**over)))]
    assert check in blocked


def test_remote_request_blocks():
    assert "network" in [c["id"] for c in preflight.blocking(run(local=False))]


def test_unverified_mission_blocks():
    m = mission()
    m["plan"]["verified"] = False
    assert "mission" in [c["id"] for c in preflight.blocking(run(m=m))]


def test_far_home_blocks():
    link = FakeLink(home={"lat": HOME[0] + 0.02, "lon": HOME[1]})    # ~2 km away
    assert "home_near" in [c["id"] for c in preflight.blocking(run(link))]


def test_long_flight_blocks():
    m = mission()
    m["plan"]["est_battery_pct"] = 90
    assert "endurance" in [c["id"] for c in preflight.blocking(run(m=m))]


def test_camera_offline_only_warns():
    checks = run(cam="offline: unplugged")
    assert preflight.blocking(checks) == []
    assert next(c for c in checks if c["id"] == "camera")["status"] == "warn"


def test_tokens_single_use_and_expiry(monkeypatch):
    t = preflight.Tokens()
    tok, _ = t.issue("M1")
    assert t.redeem(tok, "M2") is not None                       # wrong mission (and now consumed)
    tok, _ = t.issue("M1")
    assert t.redeem(tok, "M1") is None
    assert t.redeem(tok, "M1") is not None                       # replay
    tok, _ = t.issue("M1")
    real = preflight.now
    monkeypatch.setattr(preflight, "now", lambda: real() + 61)
    assert "expired" in t.redeem(tok, "M1")


# ---------------------------------------------------------------- store + analytics
@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "farm.db")


def leaves(h, p, u=0):
    return [{"label": "healthy", "status": "confident"}] * h + [{"label": "early_blight", "status": "confident"}] * p \
        + [{"label": "late_blight", "status": "uncertain"}] * u + [{"label": "no_tomato_leaf", "status": "confident"}]


def test_kinds_and_counts():
    assert kind_of("healthy") == "healthy" and kind_of("no_tomato_leaf") == "none"
    assert kind_of("nitrogen_deficiency") == "deficiency" and kind_of("spider_mites") == "pest"
    assert kind_of("late_blight") == "disease"
    assert leaf_counts(leaves(3, 2, 1)) == (6, 3, 2, 1)


def test_health_needs_enough_leaves():
    assert analytics.health_from_rows([{"n_healthy": 5, "n_problem": 5, "n_uncertain": 0}])["pct"] is None
    h = analytics.health_from_rows([{"n_healthy": 15, "n_problem": 5, "n_uncertain": 9}])
    assert h["pct"] == 75.0 and h["uncertain"] == 9


def test_field_summary_and_change(store):
    f = store.add_field("A", rect(40, 30))
    for k, (h, p) in enumerate(((20, 10), (25, 5))):
        m = store.add_mission(f["id"], {"params": {}}, True)
        store.update_mission(m["id"], status="completed", started=1000 + k * 100)
        store.add_detection({"t": 1000 + k * 100, "label": "early_blight", "status": "confident", "p": 0.9,
                             "mission_id": m["id"], "field_id": f["id"], "leaves": leaves(h, p)})
    s = analytics.field_summary(store, f)
    assert s["health"]["pct"] == pytest.approx(83.3, abs=0.1)
    assert s["change"]["health_delta"] == pytest.approx(83.3 - 66.7, abs=0.1)
    assert s["problems"] == {"early_blight": 1}


def test_field_without_inspection(store):
    f = store.add_field("B", rect(40, 30))
    s = analytics.field_summary(store, f)
    assert s["health"]["pct"] is None and "No inspection" in s["health"]["reason"]


def test_detection_filters_and_review(store):
    for i, lab in enumerate(("late_blight", "healthy", "leaf_miner")):
        store.add_detection({"id": f"d{i}", "t": 100 + i, "label": lab, "p": 0.5 + i / 10, "status": "confident",
                             "lat": 25.3 if i else None, "lon": 55.4 if i else None})
    assert store.detections(kind="disease")[0] == 1
    assert store.detections(mapped_only=True)[0] == 2
    assert [r["id"] for r in store.detections(sort="p", order="asc")[1]] == ["d0", "d1", "d2"]
    assert store.detections(q="miner")[0] == 1
    store.review_detection("d0", "dismissed", "leaf was dirty")
    assert store.detections(review="dismissed")[1][0]["review_note"] == "leaf was dirty"


def test_legacy_import_once(store, tmp_path):
    log = tmp_path / "detections.jsonl"
    log.write_text('{"id":"a","t":1,"label":"healthy","p":0.9,"lat":null}\n{"bad json\n'
                   '{"id":"b","t":2,"label":"late_blight","p":0.8,"lat":25.3,"lon":55.4,"alt_rel":2}\n')
    assert store.import_jsonl(log) == 2
    assert store.import_jsonl(log) == 0
    assert store.detection("b")["alt"] == 2 and store.detection("b")["kind"] == "disease"


def test_coverage_full_and_partial():
    field = {"polygon": rect(40, 30)}
    f = LocalFrame(*field["polygon"][0])
    # a lawn-mower track at 10 m (swath 14 m) every 7 m covers everything
    track = []
    for k, y in enumerate(range(0, 31, 7)):
        xs = (0, 40) if k % 2 == 0 else (40, 0)
        for x in xs:
            la, lo = f.to_latlon(x, y)
            track.append({"lat": la, "lon": lo, "alt": 10})
    full = analytics.coverage(field, track, 70, plan_alt=10)
    assert full["pct"] > 95
    half = analytics.coverage(field, track[:2], 70, plan_alt=10)
    assert 10 < half["pct"] < 50
    low = analytics.coverage(field, [{**t, "alt": 2} for t in track], 70, plan_alt=10)
    assert low["pct"] == 0          # climb/descent below 60 % of plan altitude does not count


# ---------------------------------------------------------------- local-only guard
def test_local_only_guard(monkeypatch, tmp_path):
    monkeypatch.setenv("CONSOLE_DEV", "1")
    monkeypatch.setenv("CONSOLE_DATA", str(tmp_path))
    import importlib
    import config
    importlib.reload(config)
    app_mod = importlib.import_module("app")
    c = app_mod.app.test_client()
    r = c.post("/api/mission/land", json={"confirm": True}, headers={"CF-Connecting-IP": "8.8.8.8"})
    assert r.status_code == 403 and r.get_json()["code"] == "remote"
    r = c.post("/api/fields", json={"name": "x"}, environ_base={"REMOTE_ADDR": "8.8.8.8"})
    assert r.status_code == 403
    r = c.post("/api/mission/land", json={"confirm": True}, environ_base={"REMOTE_ADDR": "192.168.1.5"})
    assert r.status_code != 403      # local: reaches the handler (no link -> 503)
    r = c.post("/api/mission/land", json={}, environ_base={"REMOTE_ADDR": "192.168.1.5"})
    assert r.status_code == 400      # explicit confirmation required
    assert c.get("/api/settings").status_code == 200
    r = c.put("/api/settings", json={"endurance_min": 500}, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 400


def test_real_mode_safety_gates_block():
    class Unsafe(FakeLink):
        def param(self, name):
            return {"FENCE_ENABLE": 0, "RTL_ALT_M": 15, "ARMING_CHECK": 0, "FS_THR_ENABLE": 0, "BATT_FS_LOW_ACT": 0}.get(name)
    preflight._param_cache.clear()
    checks = preflight.run_checks(Unsafe(), mission(), {"name": "A"}, SETTINGS, "live", True, real=True)
    blocked = {c["id"] for c in preflight.blocking(checks)}
    assert {"arming_checks", "rc_failsafe", "batt_failsafe", "fence"} <= blocked
    preflight._param_cache.clear()
    sim = preflight.run_checks(Unsafe(), mission(), {"name": "A"}, SETTINGS, "live", True, real=False)
    assert preflight.blocking(sim) == []          # advisory in the simulator


def test_real_mode_passes_when_configured():
    class Safe(FakeLink):
        def param(self, name):
            return {"FENCE_ENABLE": 1, "RTL_ALT_M": 15, "ARMING_CHECK": 1, "FS_THR_ENABLE": 1, "BATT_FS_LOW_ACT": 2}.get(name)
    preflight._param_cache.clear()
    assert preflight.blocking(preflight.run_checks(Safe(), mission(), {"name": "A"}, SETTINGS, "live", True, real=True)) == []


def test_connection_url_validation():
    from connection import valid_url
    for ok in ("/dev/ttyAMA0", "tcp:farm-sitl:5760", "tcp:192.168.1.20:5763", "udp:0.0.0.0:14550", None):
        assert valid_url(ok)
    for bad in ("tcp:x;rm -rf /:1", "http://evil", "/etc/passwd; ls", "tcp:host"):
        assert not valid_url(bad)
