"""The single MAVLink connection to the flight controller (Pixhawk / ArduPilot, or SITL).

A reader thread keeps a telemetry snapshot. Requests from the web server go through the
thread-safe methods below. Every command waits for the autopilot's own answer:
  * command()       -> COMMAND_ACK result (accepted / denied / unsupported / failed / timeout)
  * set_mode()      -> ack, then CONFIRMED only when a HEARTBEAT reports the new mode
  * arm()           -> ack, then confirmed by HEARTBEAT armed flag (never forced)
  * upload_mission  -> MISSION_COUNT / MISSION_REQUEST_INT / MISSION_ACK, then read-back verify
Nothing is ever reported as successful unless the autopilot said so.
"""
import queue
import threading
import time
from collections import deque

from config import MAVLINK_BAUD, MAVLINK_URL, now

ACK_NAMES = {0: "accepted", 1: "temporarily rejected", 2: "denied", 3: "unsupported", 4: "failed",
             5: "in progress", 6: "cancelled"}
MISSION_RESULT = {0: "accepted", 1: "error", 2: "unsupported frame", 3: "unsupported command",
                  4: "no space", 5: "invalid", 13: "invalid sequence", 14: "denied", 15: "operation cancelled"}
EKF_POS_HORIZ_ABS = 16      # EKF_STATUS_REPORT flag: absolute horizontal position OK
SENSOR_PREARM = 1 << 28     # SYS_STATUS: pre-arm checks passing


class CommandResult(dict):
    """{'ok': bool, 'stage': 'ack'|'confirm'|'timeout'|'error', 'detail': str, ...}"""


class MavLink(threading.Thread):
    def __init__(self, url=MAVLINK_URL, baud=MAVLINK_BAUD):
        super().__init__(daemon=True)
        self.url, self.baud = url, baud
        self.m = None
        self.state = {}
        self.messages = deque(maxlen=50)
        self.history = deque(maxlen=300)          # (t, battery V, relative altitude m)
        self.last_heartbeat = 0.0
        self.target = (1, 1)
        self.data_lock = threading.Lock()
        self.tx_lock = threading.Lock()            # one sender at a time
        self.cmd_lock = threading.Lock()           # one command/transaction at a time
        self.waiters = []                          # [(types, predicate, queue)]
        self.waiters_lock = threading.Lock()
        self.position_listeners = []
        self.error = None
        self._halt = threading.Event()

    def stop(self, timeout=5.0):
        """Close the connection and end the reader thread (used when switching sim <-> real)."""
        self._halt.set()
        try:
            if self.m:
                self.m.close()
        except Exception:
            pass
        if self.is_alive():
            self.join(timeout)
        self.last_heartbeat = 0.0

    # ---------------------------------------------------------------- reader
    def run(self):
        from pymavlink import mavutil
        self.mavutil = mavutil
        while not self._halt.is_set():
            try:
                kw = {"baud": self.baud} if not self.url.startswith(("tcp:", "udp")) else {}
                self.m = mavutil.mavlink_connection(self.url, source_system=255, source_component=190, **kw)
                self.error = None
                last_ping, last_hist, last_req = 0.0, 0.0, 0.0
                while not self._halt.is_set():
                    t = now()
                    if t - last_ping > 1:
                        self._send(lambda: self.m.mav.heartbeat_send(mavutil.mavlink.MAV_TYPE_GCS,
                                                                     mavutil.mavlink.MAV_AUTOPILOT_INVALID, 0, 0, 0))
                        last_ping = t
                    if t - last_req > 10 and self.last_heartbeat:
                        self._request_streams()
                        last_req = t
                    msg = self.m.recv_match(blocking=True, timeout=1)
                    if msg is None or msg.get_type() == "BAD_DATA":
                        continue
                    with self.data_lock:
                        self._handle(msg)
                        if now() - last_hist >= 1 and "battery_v" in self.state:
                            self.history.append((round(now(), 1), self.state.get("battery_v"),
                                                 self.state.get("alt_rel")))
                            last_hist = now()
                    self._dispatch(msg)
            except Exception as exc:  # serial unplugged, SITL restarted...: report and retry
                if self._halt.is_set():
                    break
                self.error = str(exc)
                self.last_heartbeat = 0.0
                self._halt.wait(3)

    def _request_streams(self):
        mav = self.mavutil.mavlink
        sysid, comp = self.target
        self._send(lambda: self.m.mav.request_data_stream_send(sysid, comp, mav.MAV_DATA_STREAM_ALL, 4, 1))
        for msg_id, hz in ((242, 0.5), (193, 1)):   # HOME_POSITION, EKF_STATUS_REPORT
            self._send(lambda m=msg_id, h=hz: self.m.mav.command_long_send(
                sysid, comp, mav.MAV_CMD_SET_MESSAGE_INTERVAL, 0, m, 1e6 / h, 0, 0, 0, 0, 0))
        self._send(lambda: self.m.mav.command_long_send(
            sysid, comp, mav.MAV_CMD_GET_HOME_POSITION, 0, 0, 0, 0, 0, 0, 0, 0))

    def _send(self, fn):
        with self.tx_lock:
            fn()

    def _handle(self, msg):
        kind, s = msg.get_type(), self.state
        if kind == "HEARTBEAT":
            if msg.autopilot == self.mavutil.mavlink.MAV_AUTOPILOT_INVALID:
                return  # another GCS or a companion component, not the autopilot
            self.target = (msg.get_srcSystem(), msg.get_srcComponent())
            self.last_heartbeat = now()
            s.update(mode=self.mavutil.mode_string_v10(msg), custom_mode=msg.custom_mode,
                     armed=bool(msg.base_mode & 128), system_status=msg.system_status,
                     vehicle_type=msg.type, heartbeat_t=now())
        elif kind == "SYS_STATUS":
            s.update(battery_v=round(msg.voltage_battery / 1000, 2),
                     battery_a=round(msg.current_battery / 100, 2) if msg.current_battery >= 0 else None,
                     battery_pct=msg.battery_remaining if msg.battery_remaining >= 0 else None,
                     prearm_ok=bool(msg.onboard_control_sensors_health & SENSOR_PREARM))
        elif kind == "GPS_RAW_INT":
            h_acc = getattr(msg, "h_acc", 0) or 0
            s.update(gps_fix=msg.fix_type, gps_sats=msg.satellites_visible,
                     gps_hdop=round(msg.eph / 100, 2) if msg.eph != 65535 else None,
                     gps_h_acc_m=round(h_acc / 1000, 2) if 0 < h_acc < 4_000_000_000 else None)
        elif kind == "GLOBAL_POSITION_INT":
            s.update(lat=msg.lat / 1e7, lon=msg.lon / 1e7, alt_rel=round(msg.relative_alt / 1000, 2),
                     alt_msl=round(msg.alt / 1000, 2), heading=msg.hdg / 100 if msg.hdg != 65535 else None,
                     position_t=now())
            for fn in list(self.position_listeners):
                try:
                    fn(dict(s))
                except Exception:
                    pass
        elif kind == "ATTITUDE":
            s.update(roll=round(msg.roll * 57.2958, 1), pitch=round(msg.pitch * 57.2958, 1),
                     yaw=round(msg.yaw * 57.2958, 1))
        elif kind == "VFR_HUD":
            s.update(groundspeed=round(msg.groundspeed, 1), climb=round(msg.climb, 2), throttle=msg.throttle)
        elif kind == "RC_CHANNELS":
            s.update(rc_ok=msg.chan3_raw > 0, rc_rssi=msg.rssi, rc_t=now())
        elif kind == "HOME_POSITION":
            s.update(home={"lat": msg.latitude / 1e7, "lon": msg.longitude / 1e7, "alt_msl": msg.altitude / 1000},
                     home_t=now())
        elif kind == "EKF_STATUS_REPORT":
            s.update(ekf_flags=msg.flags, ekf_pos_ok=bool(msg.flags & EKF_POS_HORIZ_ABS), ekf_t=now())
        elif kind == "MISSION_CURRENT":
            s.update(mission_seq=msg.seq, mission_total=getattr(msg, "total", None) or None)
        elif kind == "MISSION_ITEM_REACHED":
            s.update(mission_reached=msg.seq, mission_reached_t=now())
        elif kind == "STATUSTEXT":
            self.messages.appendleft({"t": now(), "text": msg.text, "severity": msg.severity})

    def _dispatch(self, msg):
        kind = msg.get_type()
        with self.waiters_lock:
            for types, pred, q in self.waiters:
                if kind in types and pred(msg):
                    q.put(msg)

    def _wait(self, types, pred, timeout):
        q = queue.Queue()
        entry = (set(types), pred, q)
        with self.waiters_lock:
            self.waiters.append(entry)
        return entry

    def _collect(self, entry, timeout):
        try:
            return entry[2].get(timeout=timeout)
        except queue.Empty:
            return None
        finally:
            pass

    def _unwait(self, entry):
        with self.waiters_lock:
            if entry in self.waiters:
                self.waiters.remove(entry)

    # ---------------------------------------------------------------- read API
    @property
    def connected(self):
        return now() - self.last_heartbeat < 3

    def snapshot(self):
        with self.data_lock:
            s = dict(self.state)
            s["messages"] = list(self.messages)[:12]
            s["history"] = list(self.history)
        s["link"] = self.connected
        s["link_error"] = self.error
        s["heartbeat_age_s"] = round(now() - self.last_heartbeat, 1) if self.last_heartbeat else None
        if s.get("position_t"):
            s["position_age_s"] = round(now() - s["position_t"], 2)
        return s

    def mode_id(self, name):
        mapping = self.m.mode_mapping() if self.m else None
        return mapping.get(name) if mapping else None

    # ---------------------------------------------------------------- commands
    def _require_link(self):
        if not self.m or not self.connected:
            return CommandResult(ok=False, stage="error", detail="No link to the flight controller.")
        return None

    def command(self, cmd, params=(0, 0, 0, 0, 0, 0, 0), timeout=3.0, label=None):
        """Send COMMAND_LONG and wait for its COMMAND_ACK."""
        err = self._require_link()
        if err:
            return err
        sysid, comp = self.target
        p = list(params) + [0] * (7 - len(params))
        with self.cmd_lock:
            entry = self._wait(["COMMAND_ACK"], lambda m: m.command == cmd, timeout)
            try:
                self._send(lambda: self.m.mav.command_long_send(sysid, comp, cmd, 0, *p))
                deadline = now() + timeout
                while True:
                    ack = self._collect(entry, max(0.05, deadline - now()))
                    if ack is None:
                        return CommandResult(ok=False, stage="timeout", detail=f"No acknowledgement within {timeout:g} s.",
                                             command=label or cmd)
                    if ack.result == 5:  # in progress: keep waiting for the final answer
                        continue
                    name = ACK_NAMES.get(ack.result, f"result {ack.result}")
                    return CommandResult(ok=ack.result == 0, stage="ack", result=ack.result, detail=name,
                                         command=label or cmd)
            finally:
                self._unwait(entry)

    def _confirm(self, check, timeout):
        deadline = now() + timeout
        while now() < deadline:
            with self.data_lock:
                if check(self.state):
                    return True
            time.sleep(0.1)
        return False

    def set_mode(self, name, timeout=4.0):
        err = self._require_link()
        if err:
            return err
        mode = self.mode_id(name)
        if mode is None:
            return CommandResult(ok=False, stage="error", detail=f"The autopilot has no {name} mode.", command=f"mode {name}")
        res = self.command(176, (1, mode), label=f"mode {name}")  # MAV_CMD_DO_SET_MODE, custom mode
        if not res["ok"]:
            return res
        confirmed = self._confirm(lambda s: s.get("custom_mode") == mode, timeout)
        res.update(ok=confirmed, stage="confirm" if confirmed else "timeout",
                   detail="Mode change confirmed by telemetry." if confirmed
                   else f"Acknowledged, but telemetry still shows {self.state.get('mode')} after {timeout:g} s.")
        return res

    def arm(self, timeout=6.0):
        res = self.command(400, (1, 0), timeout=5, label="arm")   # MAV_CMD_COMPONENT_ARM_DISARM, never forced
        if not res["ok"]:
            return res
        confirmed = self._confirm(lambda s: s.get("armed") is True, timeout)
        res.update(ok=confirmed, stage="confirm" if confirmed else "timeout",
                   detail="Armed (confirmed by telemetry)." if confirmed else "Acknowledged, but not armed.")
        return res

    def mission_start(self):
        return self.command(300, (0, 0), label="mission start")   # MAV_CMD_MISSION_START

    def param(self, name, timeout=3.0):
        err = self._require_link()
        if err:
            return None
        sysid, comp = self.target
        with self.cmd_lock:
            entry = self._wait(["PARAM_VALUE"], lambda m: m.param_id == name, timeout)
            try:
                self._send(lambda: self.m.mav.param_request_read_send(sysid, comp, name.encode(), -1))
                msg = self._collect(entry, timeout)
                return float(msg.param_value) if msg else None
            finally:
                self._unwait(entry)

    # ---------------------------------------------------------------- missions
    def upload_mission(self, items, timeout=4.0):
        """Mission protocol upload. Returns CommandResult(ok, detail, count)."""
        err = self._require_link()
        if err:
            return err
        mav = self.mavutil.mavlink
        sysid, comp = self.target
        n = len(items)
        with self.cmd_lock:
            entry = self._wait(["MISSION_REQUEST_INT", "MISSION_REQUEST", "MISSION_ACK"], lambda m: True, timeout)
            try:
                self._send(lambda: self.m.mav.mission_count_send(sysid, comp, n, mav.MAV_MISSION_TYPE_MISSION))
                sent = set()
                while True:
                    msg = self._collect(entry, timeout)
                    if msg is None:
                        return CommandResult(ok=False, stage="timeout",
                                             detail=f"Upload stalled after {len(sent)}/{n} items.", count=n)
                    if msg.get_type() == "MISSION_ACK":
                        if msg.type == 0 and len(sent) >= n:
                            return CommandResult(ok=True, stage="ack", detail=f"{n} items accepted.", count=n)
                        return CommandResult(ok=False, stage="ack", count=n,
                                             detail="Rejected: " + MISSION_RESULT.get(msg.type, f"code {msg.type}"))
                    seq = msg.seq
                    if not 0 <= seq < n:
                        continue
                    it = items[seq]
                    self._send(lambda it=it: self.m.mav.mission_item_int_send(
                        sysid, comp, it["seq"], it["frame"], it["command"], 0, 1,
                        it["param1"], it["param2"], it["param3"], it["param4"], it["x"], it["y"], it["z"],
                        mav.MAV_MISSION_TYPE_MISSION))
                    sent.add(seq)
            finally:
                self._unwait(entry)

    def download_mission(self, timeout=4.0):
        """Read the mission back from the autopilot (for verification)."""
        err = self._require_link()
        if err:
            return None
        mav = self.mavutil.mavlink
        sysid, comp = self.target
        with self.cmd_lock:
            entry = self._wait(["MISSION_COUNT", "MISSION_ITEM_INT"], lambda m: True, timeout)
            try:
                self._send(lambda: self.m.mav.mission_request_list_send(sysid, comp, mav.MAV_MISSION_TYPE_MISSION))
                msg = self._collect(entry, timeout)
                if msg is None or msg.get_type() != "MISSION_COUNT":
                    return None
                count, items = msg.count, []
                for seq in range(count):
                    for _ in range(3):
                        self._send(lambda s=seq: self.m.mav.mission_request_int_send(
                            sysid, comp, s, mav.MAV_MISSION_TYPE_MISSION))
                        got = self._collect(entry, timeout)
                        while got is not None and not (got.get_type() == "MISSION_ITEM_INT" and got.seq == seq):
                            got = self._collect(entry, timeout)
                        if got is not None:
                            items.append({"seq": got.seq, "command": got.command, "x": got.x, "y": got.y,
                                          "z": round(got.z, 2)})
                            break
                    else:
                        return None
                self._send(lambda: self.m.mav.mission_ack_send(sysid, comp, 0, mav.MAV_MISSION_TYPE_MISSION))
                return items
            finally:
                self._unwait(entry)


def mission_matches(planned, readback):
    """Compare uploaded items with the read-back (item 0 is home, rewritten by ArduPilot)."""
    if readback is None or len(readback) != len(planned):
        return False, "Read-back length differs from the plan." if readback is not None else "Read-back failed."
    for p, r in zip(planned[1:], readback[1:]):
        if p["command"] != r["command"]:
            return False, f"Item {p['seq']}: command differs."
        if p["command"] == 16 and (abs(p["x"] - r["x"]) > 2 or abs(p["y"] - r["y"]) > 2 or abs(p["z"] - r["z"]) > 0.2):
            return False, f"Item {p['seq']}: waypoint differs."
    return True, f"All {len(planned)} items verified."
