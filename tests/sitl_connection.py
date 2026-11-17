#!/usr/bin/env python3
"""Simulator <-> real-drone switching, against the devstack (no real drone involved).

    python3 tests/sitl_connection.py [--base http://localhost:8090]

"Real" is pointed at a second SITL port (tcp:farm-sitl:5762) so the real-mode code path runs:
separate database, stricter safety gates (SITL's default params leave the battery failsafe and
geofence off, so real mode must refuse to launch). Ends switched back to the simulator.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request

P = argparse.ArgumentParser()
P.add_argument("--base", default="http://localhost:8090")
A = P.parse_args()
FAILS = []


def call(method, path, body=None, headers=None):
    req = urllib.request.Request(A.base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  ({info})" if info else ""))
    if not ok:
        FAILS.append(name)


def wait_link(timeout=40):
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = call("GET", "/api/state")[1]
        if s["telemetry"].get("link") and s["telemetry"].get("home"):
            return s
        time.sleep(1)
    return call("GET", "/api/state")[1]


def field_and_mission(home, name):
    la, lo = home["lat"] + 0.0002, home["lon"] + 0.0002
    poly = [[la, lo], [la, lo + 0.0004], [la + 0.0003, lo + 0.0004], [la + 0.0003, lo]]
    f = call("POST", "/api/fields", {"name": name, "polygon": poly})[1]
    m = call("POST", "/api/missions", {"field_id": f["id"], "altitude_m": 10})[1]
    return f, m


code, info = call("GET", "/api/connection")
assert info["mode"] in ("sim", "real")
ORIG_REAL = info["urls"]["real"] if info["urls"]["real"] != "tcp:farm-sitl:5762" else None
call("POST", "/api/connection", {"mode": "sim"})
call("PUT", "/api/connection", {"real_url": "tcp:farm-sitl:5762"})
sim_missions = len(call("GET", "/api/missions")[1])

code, r = call("POST", "/api/connection", {"mode": "real"})
check("real needs explicit confirmation", code == 400, r.get("error"))
code, r = call("POST", "/api/connection", {"mode": "real", "confirm": True}, {"CF-Connecting-IP": "203.0.113.5"})
check("switch refused through the public link", code == 403)
code, r = call("PUT", "/api/connection", {"real_url": "tcp:x;rm -rf /:1"})
check("invalid address rejected", code == 400)

code, r = call("POST", "/api/connection", {"mode": "real", "confirm": True})
check("switched to real", code == 200 and r["mode"] == "real" and not r["sim"], r)
s = wait_link()
check("real link up, SIMULATOR flag off", s["telemetry"].get("link") and s["sim"] is False and s["mode"] == "real")
f, m = field_and_mission(s["telemetry"]["home"], f"real gate {time.strftime('%H:%M:%S')}")
check("real mission is not marked as simulated", m.get("sim") is False)
check("real records separate from sim", all(not x["sim"] for x in call("GET", "/api/missions")[1]))
code, up = call("POST", f"/api/missions/{m['id']}/upload")
check("upload + verify on real link", code == 200 and up["plan"]["verified"], up.get("error"))
pf = None
for _ in range(20):
    pf = call("GET", f"/api/missions/{m['id']}/preflight")[1]
    if {"batt_failsafe", "fence"} <= set(pf["blocking"]):
        break
    time.sleep(2)
crit = {c["id"]: c for c in pf["checks"] if c["critical"]}
check("safety gates are critical in real mode", all(k in crit for k in ("arming_checks", "rc_failsafe", "batt_failsafe", "fence")))
check("battery failsafe off blocks real launch", "batt_failsafe" in pf["blocking"], crit.get("batt_failsafe", {}).get("detail"))
check("geofence off blocks real launch", "fence" in pf["blocking"])
check("no launch token in real mode while gates fail", "token" not in pf and not pf["ready"])
code, r = call("POST", f"/api/missions/{m['id']}/start", {"confirm": True, "token": "x"})
check("start refused", code in (403, 409))

call("DELETE", f"/api/missions/{m['id']}")
call("DELETE", f"/api/fields/{f['id']}")
code, r = call("POST", "/api/connection", {"mode": "sim"})
call("PUT", "/api/connection", {"real_url": ORIG_REAL})
check("switched back to simulator", code == 200 and r["mode"] == "sim")
s = wait_link()
check("sim link up again", s["telemetry"].get("link") and s["sim"] is True)
check("sim records intact", len(call("GET", "/api/missions")[1]) == sim_missions)
f2, m2 = field_and_mission(s["telemetry"]["home"], f"sim gate {time.strftime('%H:%M:%S')}")
call("POST", f"/api/missions/{m2['id']}/upload")
for _ in range(20):
    pf = call("GET", f"/api/missions/{m2['id']}/preflight")[1]
    if pf["ready"]:
        break
    time.sleep(2)
adv = {c["id"]: c for c in pf["checks"]}
check("same gates are advisory in the simulator", not adv["fence"]["critical"] and pf["ready"], pf["blocking"])
call("DELETE", f"/api/missions/{m2['id']}")
print("\nALL PASSED" if not FAILS else f"\n{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
