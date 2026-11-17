#!/usr/bin/env python3
"""End-to-end mission test against the ArduPilot SITL devstack (devstack/up.sh).

    python3 tests/sitl_mission.py [--base http://localhost:8090] [--full]

Checks: field -> plan -> upload + read-back -> start refused without/with bad token ->
preflight -> launch -> takeoff -> surveying (progress grows, captures stored) -> pause (BRAKE
confirmed) -> resume (AUTO) -> [--full: fly the whole survey | default: RTL] -> landed ->
mission completed/aborted with track, stats and events. Never run against the real drone.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request

P = argparse.ArgumentParser()
P.add_argument("--base", default="http://localhost:8090")
P.add_argument("--full", action="store_true", help="let the survey finish instead of RTL halfway")
A = P.parse_args()
FAILS = []


def call(method, path, body=None, expect=None):
    req = urllib.request.Request(A.base + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            code, data = r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        code, data = e.code, json.loads(e.read() or b"null")
    if expect is not None and code != expect:
        raise SystemExit(f"FAIL {method} {path}: expected {expect}, got {code}: {data}")
    return code, data


def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  ({info})" if info else ""))
    if not ok:
        FAILS.append(name)


def state():
    return call("GET", "/api/state")[1]


def wait(name, pred, timeout):
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = state()
        if pred(s):
            check(name, True, f"{time.time() - t0:.0f} s")
            return s
        time.sleep(1)
    check(name, False, "timeout")
    return state()


sys_ = call("GET", "/api/system", expect=200)[1]
assert sys_["sim"], "Refusing to run: the console is not in simulator mode."
wait("telemetry link", lambda s: s["telemetry"].get("link"), 30)
home = wait("home position", lambda s: s["telemetry"].get("home"), 60)["telemetry"]["home"]

# A 60 x 40 m field ~30 m north-east of home.
la, lo = home["lat"] + 0.0003, home["lon"] + 0.0003
dla, dlo = 40 / 111320, 60 / (111320 * 0.904)
poly = [[la, lo], [la, lo + dlo], [la + dla, lo + dlo], [la + dla, lo]]
code, bad = call("POST", "/api/fields", {"name": "x", "polygon": [[0, 0], [1, 1], [0, 1], [1, 0]]})
check("self-crossing field rejected", code == 400, bad.get("error"))
field = call("POST", "/api/fields", {"name": f"SITL test {time.strftime('%H:%M:%S')}", "polygon": poly}, 201)[1]
check("field area", 2000 < field["area_m2"] < 2800, f"{field['area_m2']:.0f} m²")

preview = call("POST", "/api/missions/plan", {"field_id": field["id"], "altitude_m": 10, "speed_ms": 4}, 200)[1]
check("plan preview", preview["lines"] >= 2, f"{preview['lines']} lines, {preview['total_length_m']} m")
m = call("POST", "/api/missions", {"field_id": field["id"], "altitude_m": 10, "speed_ms": 4}, 201)[1]
mid = m["id"]

code, r = call("POST", f"/api/missions/{mid}/start", {"confirm": True, "token": "nope"})
check("start refused before upload / with a bad token", code == 403, r.get("error"))
pf = call("GET", f"/api/missions/{mid}/preflight", expect=200)[1]
check("preflight blocks un-uploaded mission", "mission" in pf["blocking"] and "token" not in pf, pf["blocking"])

up = call("POST", f"/api/missions/{mid}/upload", expect=200)[1]
check("upload + read-back verified", up["status"] == "uploaded" and up["plan"]["verified"])

pf = None
for _ in range(60):  # EKF / GPS can need a while after SITL boot
    pf = call("GET", f"/api/missions/{mid}/preflight", expect=200)[1]
    if pf["ready"]:
        break
    time.sleep(2)
check("preflight passes in SITL", pf["ready"], [c["id"] + ":" + c["detail"] for c in pf["checks"] if c["status"] == "fail"])
if not pf["ready"]:
    sys.exit(1)
code, r = call("POST", f"/api/missions/{mid}/start", {"token": pf["token"]})
check("start without explicit confirm refused", code == 400)
code, r = call("POST", f"/api/missions/{mid}/start", {"confirm": True, "token": pf["token"] + "x"})
check("start with a wrong token refused", code == 403)
pf = call("GET", f"/api/missions/{mid}/preflight", expect=200)[1]
code, r = call("POST", f"/api/missions/{mid}/start", {"confirm": True, "token": pf["token"]})
check("launch accepted", code == 200, r.get("error") or r.get("status"))
if code != 200:
    sys.exit(1)
code, r = call("POST", f"/api/missions/{mid}/start", {"confirm": True, "token": pf["token"]})
check("token cannot be replayed", code == 403)

wait("airborne + taking off", lambda s: (s["telemetry"].get("alt_rel") or 0) > 2, 60)
s = wait("surveying", lambda s: (s["mission"]["active"] or {}).get("status") == "surveying", 120)
p0 = s["mission"]["active"]["progress_pct"]
s = wait("progress increases", lambda s: (s["mission"]["active"] or {}).get("progress_pct", 0) > p0 + 10, 90)
check("ETA reported", s["mission"]["active"]["eta_s"] is not None, s["mission"]["active"]["eta_s"])

code, r = call("POST", "/api/mission/pause", {"confirm": True})
check("pause acknowledged + confirmed", code == 200 and r["ok"] and r["stage"] == "confirm", r.get("detail"))
wait("stage paused", lambda s: (s["mission"]["active"] or {}).get("status") == "paused", 10)
time.sleep(3)
gs = state()["telemetry"].get("groundspeed")
check("holding position", gs is not None and gs < 0.8, f"{gs} m/s")
code, r = call("POST", "/api/mission/resume", {"confirm": True})
check("resume acknowledged + confirmed", code == 200 and r["ok"], r.get("detail"))
wait("surveying again", lambda s: (s["mission"]["active"] or {}).get("status") == "surveying", 15)
code, r = call("POST", "/api/mission/resume", {"confirm": True})
check("resume refused when not paused", code == 409, r.get("error"))

if not A.full:
    time.sleep(10)
    code, r = call("POST", "/api/mission/rtl", {"confirm": True})
    check("RTL acknowledged + confirmed", code == 200 and r["ok"], r.get("detail"))
    wait("stage returning", lambda s: (s["mission"]["active"] or {}).get("status") == "returning", 10)
wait("landed + mission closed", lambda s: s["mission"]["active"] is None and not s["telemetry"].get("armed"), 400)

md = call("GET", f"/api/missions/{mid}", expect=200)[1]
want = "completed" if A.full else "aborted"
check(f"mission status {want}", md["status"] == want, md["end_reason"])
check("track recorded", len(md["track"]) > 20, len(md["track"]))
check("stats stored", md["stats"] and md["stats"]["distance_m"] > 50, md["stats"])
check("captures stored with GPS", any(d["lat"] is not None for d in md["detections"]), len(md["detections"]))
check("coverage computed", md["coverage"] is not None, md["coverage"])
kinds = {e["kind"] for e in md["events"]}
check("events logged (ack/stage/verify)", {"ack", "stage", "verify"} <= kinds, kinds)

code, r = call("POST", "/api/mission/rtl", {"confirm": True})
check("command while disarmed refused", code == 409, r.get("error"))
req = urllib.request.Request(A.base + "/api/mission/land", method="POST", data=b'{"confirm":true}',
                             headers={"Content-Type": "application/json", "CF-Connecting-IP": "1.2.3.4"})
try:
    urllib.request.urlopen(req)
    check("tunnel request refused", False)
except urllib.error.HTTPError as e:
    check("tunnel request refused (403)", e.code == 403)
print("\nALL PASSED" if not FAILS else f"\n{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
