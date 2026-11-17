"""Hardware-facing workers moved out of app.py unchanged: camera, field sensors, AI model, Pi health."""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

import cv2

from config import AI_INTERVAL_S, CAM_H, CAM_W, CAPTURE_DIR, HERE, MODEL_V2, ROOT, STREAM_W, now

sys.path.insert(0, str(HERE.parent))
from env_risk import fuse, risks  # noqa: E402
from infer import classify  # noqa: E402  (baseline model: same preprocessing as the CLI tool)
from pipeline import LeafPipeline  # noqa: E402  (v2: leaf detector -> per-leaf classifier)

lock = threading.Lock()


# ---------------------------------------------------------------- camera
class Camera(threading.Thread):
    """Owns /dev/video0 and keeps the most recent frame."""

    def __init__(self):
        super().__init__(daemon=True)
        self.frame = None
        self.jpeg = None
        self.frame_time = 0.0
        self.fps = 0.0
        self.status = "starting"

    def configure(self):
        devices = subprocess.run(["v4l2-ctl", "--list-devices"], capture_output=True, text=True).stdout
        match = re.search(r"unicam.*?(/dev/media\d+)", devices, re.S)
        if not match:
            raise RuntimeError("camera not detected")
        subprocess.run(["media-ctl", "-d", match.group(1), "--set-v4l2",
                        f'"ar1335_mcu 10-0042":0 [fmt:UYVY8_2X8/{CAM_W}x{CAM_H}]'], check=True)
        subprocess.run(["v4l2-ctl", "-d", "/dev/video0",
                        f"--set-fmt-video=width={CAM_W},height={CAM_H},pixelformat=UYVY"], check=True)

    def run(self):
        pipeline = (f"v4l2src device=/dev/video0 io-mode=2 ! video/x-raw,format=UYVY,width={CAM_W},"
                    f"height={CAM_H} ! videoconvert ! video/x-raw,format=BGR ! "
                    "appsink drop=1 max-buffers=1 sync=false")
        while True:
            try:
                self.configure()
                cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
                if not cap.isOpened():
                    raise RuntimeError("could not open camera stream")
                self.status = "live"
                count, window = 0, now()
                while True:
                    ok, frame = cap.read()
                    if not ok:
                        raise RuntimeError("camera stopped sending frames")
                    small = cv2.resize(frame, (STREAM_W, STREAM_W * CAM_H // CAM_W),
                                       interpolation=cv2.INTER_AREA)
                    ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 72])
                    with lock:
                        self.frame, self.frame_time = frame, now()
                        if ok:
                            self.jpeg = buf.tobytes()
                    count += 1
                    if now() - window >= 2:
                        self.fps, count, window = count / (now() - window), 0, now()
            except Exception as exc:  # keep retrying; the console reports the reason
                self.status = f"offline: {exc}"
                self.fps = 0.0
                time.sleep(3)



# ---------------------------------------------------------------- field sensors
SENSOR_FIELDS = ("temp_c", "rh", "pressure_hpa", "gas_ohm", "soil_moisture_pct")
SENSOR_LIMITS = {"temp_c": (-40, 85), "rh": (0, 100), "pressure_hpa": (300, 1100),
                 "gas_ohm": (0, 5e7), "soil_moisture_pct": (0, 100)}  # BME680 + probe ranges


class SensorStore:
    """BME680 + soil-moisture readings posted by field nodes (or a LoRa gateway).

    Readings are kept for 7 days in memory and appended to captures/sensors.jsonl."""

    def __init__(self):
        self.file = CAPTURE_DIR / "sensors.jsonl"
        self.readings = deque(maxlen=20000)
        self.lock = threading.Lock()
        self._risk, self._risk_t = {}, 0.0
        if self.file.exists():
            for line in self.file.read_text().splitlines()[-20000:]:
                try:
                    self.readings.append(json.loads(line))
                except ValueError:
                    pass

    def add(self, data):
        reading = {"t": float(data.get("t") or now()), "node": str(data.get("node", "node-1"))[:32]}
        for k in SENSOR_FIELDS:
            v = data.get(k)
            if v is None:
                continue
            v = float(v)
            lo, hi = SENSOR_LIMITS[k]
            if not lo <= v <= hi:
                raise ValueError(f"{k}={v} is outside the sensor's range ({lo} to {hi})")
            reading[k] = v
        if len(reading) <= 2:
            raise ValueError(f"no readings given; send any of {', '.join(SENSOR_FIELDS)}")
        CAPTURE_DIR.mkdir(exist_ok=True)
        with self.lock:
            self.readings.append(reading)
            with open(self.file, "a") as f:
                f.write(json.dumps(reading) + "\n")
                f.flush()
                os.fsync(f.fileno())
            self._risk_t = 0.0
        return reading

    def risk(self):
        if now() - self._risk_t > 30:
            with self.lock:
                data = list(self.readings)
            self._risk = risks(data) if data else {"hours_of_data": 0, "diseases": {}}
            self._risk_t = now()
        return self._risk

    def summary(self):
        r = dict(self.risk())
        with self.lock:
            r["readings"] = len(self.readings)
            r["last_t"] = self.readings[-1]["t"] if self.readings else None
            # hourly series for the last 48 h (temperature, humidity, soil) for the charts
            series = [x for x in self.readings if now() - x["t"] <= 48 * 3600]
        r["series"] = [[round(x["t"]), x.get("temp_c"), x.get("rh"), x.get("soil_moisture_pct")] for x in series[-400:]]
        r["diseases"] = {k: {"risk": round(v[0], 2), "why": v[1]} for k, v in r.get("diseases", {}).items()}
        return r


NOT_DISEASE = {"healthy", "no_tomato_leaf"}


class Analyzer(threading.Thread):
    """Runs the v2 two-stage pipeline (leaf detector -> classifier) when its package is
    installed, otherwise the original single-frame baseline model."""

    def __init__(self, camera, sensors):
        super().__init__(daemon=True)
        self.camera, self.sensors = camera, sensors
        self.result = None
        self.net_lock = threading.Lock()  # cv2.dnn nets are not safe to share across threads
        if (MODEL_V2 / "model.json").exists():
            self.pipeline = LeafPipeline(MODEL_V2)
            self.classes = self.pipeline.classes
            self.card = self.pipeline.meta
        else:
            self.pipeline = None
            self.net = cv2.dnn.readNetFromONNX(str(ROOT / "ml" / "model.onnx"))
            self.classes = json.load(open(ROOT / "ml" / "classes.json"))
            self.card = {"name": "baseline", "classes": self.classes,
                         "classifier": {"architecture": "MobileNetV3-Small (PlantVillage only)"}}

    def _fuse(self, item, env):
        """Apply the environmental prior to one leaf/frame result (boost-only, bounded)."""
        if not item or not env.get("diseases") or "probs" not in item:
            return item
        all_p = [{"label": c, "p": p} for c, p in zip(self.classes, item["probs"])]
        fused, notes = fuse(all_p, env)
        best = fused[0]
        threshold = self.pipeline.uncertain_below if self.pipeline else 0.0
        status = "uncertain" if best["p"] < threshold and best["label"] != "no_tomato_leaf" else "confident"
        return {**item, "label": best["label"], "p": round(best["p"], 4), "status": status,
                "p_image": round(best["p_image"], 4), "env_notes": notes,
                "top": [{"label": x["label"], "p": round(x["p"], 4)} for x in fused[:3]]}

    def predict(self, frame):
        env = self.sensors.risk()
        with self.net_lock:
            t = now()
            if self.pipeline:
                r = self.pipeline.run(frame)
            else:
                top = classify(self.net, self.classes, frame)
                r = {"leaves": [], "frame": {"label": top[0][0], "p": round(top[0][1], 4), "status": "confident",
                                             "top": [{"label": k, "p": round(p, 4)} for k, p in top], "box": None}}
            ms = (now() - t) * 1000
        r["leaves"] = [self._fuse(leaf, env) for leaf in r["leaves"]]
        r["frame"] = self._fuse(r["frame"], env)
        r["headline"] = self.headline(r)
        r.update(ms=round(ms), t=now(), model=self.card.get("name"))
        return r

    @staticmethod
    def headline(r):
        """Most serious confident finding first, then healthy leaves, then the frame verdict."""
        leaves = r["leaves"]
        sick = [l for l in leaves if l["label"] not in NOT_DISEASE and l["status"] == "confident"]
        if sick:
            return max(sick, key=lambda l: l["p"])
        if leaves:
            return max(leaves, key=lambda l: l["p"])
        return r["frame"]

    def run(self):
        while True:
            frame = self.camera.frame
            if frame is not None and now() - self.camera.frame_time < 3:
                self.result = self.predict(frame)
            time.sleep(AI_INTERVAL_S)



# ---------------------------------------------------------------- system health
class SystemHealth(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.state = {}

    @staticmethod
    def run_cmd(*cmd):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout.strip()
        except Exception:
            return ""

    def run(self):
        while True:
            s = {}
            try:
                s["cpu_temp"] = round(int(open("/sys/class/thermal/thermal_zone0/temp").read()) / 1000, 1)
            except OSError:
                pass
            s["load"] = round(os.getloadavg()[0], 2)
            s["cores"] = os.cpu_count()
            try:
                mem = dict(line.split(":") for line in open("/proc/meminfo"))
                total = int(mem["MemTotal"].split()[0])
                avail = int(mem["MemAvailable"].split()[0])
                s["mem_pct"] = round(100 * (total - avail) / total)
                s["uptime_s"] = int(float(open("/proc/uptime").read().split()[0]))
            except (OSError, KeyError, ValueError):
                pass
            disk = shutil.disk_usage("/")
            s["disk_pct"] = round(100 * disk.used / disk.total)
            throttled = self.run_cmd("vcgencmd", "get_throttled").split("=")[-1]
            flags = int(throttled, 16) if throttled.startswith("0x") else 0
            s["undervoltage_now"] = bool(flags & 0x1)
            s["undervoltage_since_boot"] = bool(flags & 0x10000)
            wifi = self.run_cmd("nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL", "device", "wifi")
            active = [line.split(":") for line in wifi.splitlines() if line.startswith("yes:")]
            if active:
                s["wifi_ssid"], s["wifi_signal"] = active[0][1], int(active[0][2] or 0)
            s["ip"] = self.run_cmd("hostname", "-I").split(" ")[0]
            tunnel_log = self.run_cmd("journalctl", "-u", "crop-tunnel", "-o", "cat", "--no-pager", "-n", "300")
            links = re.findall(r"https://[a-z0-9-]+\.trycloudflare\.com", tunnel_log)
            tunnel_up = self.run_cmd("systemctl", "is-active", "crop-tunnel") == "active"
            s["public_url"] = links[-1] if links and tunnel_up else None
            s["hostname"] = self.run_cmd("hostname")
            s["t"] = now()
            self.state = s
            time.sleep(5)



# ---------------------------------------------------------------- dev sources
class FolderCamera(threading.Thread):
    """Dev mode only: cycle through still images (CONSOLE_DEV_IMAGES) as if they were the camera."""

    def __init__(self, folder):
        super().__init__(daemon=True)
        self.files = sorted(Path(folder).glob("*.jpg"))
        self.frame, self.jpeg, self.frame_time, self.fps, self.status = None, None, 0.0, 0.0, "live"

    def run(self):
        while True:
            for f in self.files:
                img = cv2.imread(str(f))
                if img is None:
                    continue
                img = cv2.resize(img, (CAM_W, CAM_H))
                ok, buf = cv2.imencode(".jpg", cv2.resize(img, (STREAM_W, STREAM_W * CAM_H // CAM_W)))
                self.frame, self.frame_time, self.fps = img, now(), 1.0
                if ok:
                    self.jpeg = buf.tobytes()
                for _ in range(8):  # hold each image ~8 s, refreshing its timestamp
                    self.frame_time = now()
                    time.sleep(1)

class NoCamera:
    """Dev mode without test images: an honest 'no camera' source."""
    frame, jpeg, frame_time, fps = None, None, 0.0, 0.0
    status = "offline: no camera in developer mode (set CONSOLE_DEV_IMAGES to replay photos)"

    def start(self):
        pass


