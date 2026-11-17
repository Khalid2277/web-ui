#!/usr/bin/env python3
"""Failure-path checks against the SITL devstack. Runs INSIDE the console container:

    docker exec -w /app farm-console python3 tests/sitl_failures.py

Uses a second MAVLink connection (SITL serial 1, tcp 5762) to break things in the simulator,
then checks the console's API reacts honestly:
  * low battery           -> preflight 'battery' fails, no launch token
  * GPS disabled          -> preflight 'gps' / 'ekf' fail
  * unsupported command   -> reported as not accepted (never as success)
  * start with no token / remote request -> refused
Every simulator parameter is restored at the end.
"""
import json
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, "/app/raspberry_pi/dashboard")
from mavlink_link import MavLink  # noqa: E402

BASE = "http://127.0.0.1:8000"
FAILS = []


def call(method, path, body=None, headers=None):
    req = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body is not None else None,
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


def set_param(link, name, value):
    link.m.mav.param_set_send(link.target[0], link.target[1], name.encode(), float(value), 9)
    time.sleep(0.5)
    got = link.param(name)
    return got is not None and abs(got - value) < 1e-3


def preflight(mid, want_fail, timeout=40):
    t0 = time.time()
    while time.time() - t0 < timeout:
        _, pf = call("GET", f"/api/missions/{mid}/preflight")
        failing = {c["id"] for c in pf["checks"] if c["status"] == "fail"}
        if want_fail <= failing:
            return pf, failing
        time.sleep(2)
    return pf, failing


aux = MavLink("tcp:farm-sitl:5762")
aux.start()
for _ in range(30):
    if aux.connected:
        break
    time.sleep(1)
assert aux.connected, "no second link to SITL"
assert call("GET", "/api/system")[1]["sim"], "console is not in simulator mode"

# A field + uploaded mission next to home.
home = call("GET", "/api/state")[1]["telemetry"]["home"]
la, lo = home["lat"] + 0.0002, home["lon"] + 0.0002
poly = [[la, lo], [la, lo + 0.0004], [la + 0.0003, lo + 0.0004], [la + 0.0003, lo]]
f = call("POST", "/api/fields", {"name": "failure test", "polygon": poly})[1]
code, m = call("POST", "/api/missions", {"field_id": f["id"], "altitude_m": 10})
mid = m["id"]
code, up = call("POST", f"/api/missions/{mid}/upload")
check("upload ok", code == 200, up.get("error"))
pf, failing = preflight(mid, set())
check("baseline preflight ready", pf["ready"], failing)

orig_v = aux.param("SIM_BATT_VOLTAGE")
try:
    # ---- low battery
    check("set SIM_BATT_VOLTAGE=10.5", set_param(aux, "SIM_BATT_VOLTAGE", 10.5))
    pf, failing = preflight(mid, {"battery"})
    check("low battery blocks launch", "battery" in failing and not pf.get("token"), failing)
    set_param(aux, "SIM_BATT_VOLTAGE", orig_v)
    pf, failing = preflight(mid, set())
    check("recovers when battery is back", "battery" not in failing, failing)

    # ---- GPS lost
    check("set SIM_GPS1_ENABLE=0", set_param(aux, "SIM_GPS1_ENABLE", 0))
    pf, failing = preflight(mid, {"gps"})
    check("no GPS blocks launch", "gps" in failing and not pf.get("token"), failing)
    code, r = call("POST", f"/api/missions/{mid}/start", {"confirm": True, "token": pf.get("token")})
    check("start refused while GPS is lost", code in (403, 409), r.get("error"))
finally:
    set_param(aux, "SIM_GPS1_ENABLE", 1)
    set_param(aux, "SIM_BATT_VOLTAGE", orig_v)

# ---- an unsupported command is never reported as success
res = aux.command(31999, label="unsupported test command")
check("unsupported command not reported as success", res["ok"] is False and res["stage"] in ("ack", "timeout"), res)

# ---- remote (tunnel) requests are view-only
code, r = call("POST", f"/api/missions/{mid}/upload", headers={"CF-Connecting-IP": "203.0.113.9"})
check("upload refused through the tunnel", code == 403)
code, r = call("GET", f"/api/missions/{mid}/preflight", headers={"CF-Connecting-IP": "203.0.113.9"})
check("tunnel preflight never issues a token", "token" not in r and "network" in r["blocking"])

pf, failing = preflight(mid, set(), timeout=90)
check("GPS recovered after restore", "gps" not in failing, failing)
call("DELETE", f"/api/missions/{mid}")
print("\nALL PASSED" if not FAILS else f"\n{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
