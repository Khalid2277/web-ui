"""Shared settings for the farm command center (read once from the environment)."""
import os
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
MODEL_V2 = ROOT / "ml" / "models" / "tomato-v2"

# Developer mode (CONSOLE_DEV=1): an isolated instance with its own data folder and no password.
# It only talks to a flight controller when MAVLINK_URL is set explicitly (e.g. the SITL simulator).
DEV = os.environ.get("CONSOLE_DEV") == "1"
PORT = int(os.environ.get("CONSOLE_PORT", "8000"))
HOST = os.environ.get("CONSOLE_HOST") or ("127.0.0.1" if DEV else "0.0.0.0")
CAPTURE_DIR = Path(os.environ["CONSOLE_DATA"]) if os.environ.get("CONSOLE_DATA") else HERE / "captures"
DB_FILE = CAPTURE_DIR / "farm.db"
LOG_FILE = CAPTURE_DIR / "detections.jsonl"  # legacy log, imported into the database once

MAVLINK_URL = os.environ.get("MAVLINK_URL", "/dev/ttyAMA0")
MAVLINK_BAUD = int(os.environ.get("MAVLINK_BAUD", "57600"))
TELEMETRY_ENABLED = not DEV or "MAVLINK_URL" in os.environ
# Set when the flight controller is the ArduPilot SITL simulator; the UI shows a SIMULATOR banner.
SIM = os.environ.get("CONSOLE_SIM") == "1"

CAM_W, CAM_H = 1920, 1080
STREAM_W = 960
AI_INTERVAL_S = 1.5


def now():
    return time.time()
