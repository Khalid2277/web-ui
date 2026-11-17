"""Which flight controller the console is talking to: the simulator (ArduPilot SITL) or the real drone.

Each mode has its own MAVLink address and its own database, so simulated flights, detections and
statistics never mix with real field records. The rest of the app holds `link` and `store`
proxies that always point at the current mode, so switching needs no restart.

Switching is refused while a mission is active or the drone is armed.
"""
import json
import os
import re
import threading

from config import CAPTURE_DIR, DEV, MAVLINK_BAUD
from mavlink_link import MavLink
from store import Store

MODES = ("sim", "real")
FILE = CAPTURE_DIR / "connection.json"
DB = {"real": CAPTURE_DIR / "farm.db", "sim": CAPTURE_DIR / "farm-sim.db"}
URL_RE = re.compile(r"^(tcp:[\w.\-]+:\d{2,5}|udp(in|out)?:[\w.\-]*:\d{2,5}|/dev/[\w./\-]+)$")


def valid_url(url):
    return url is None or bool(URL_RE.match(url))


class Proxy:
    """Forwards everything to the current target object."""

    def __init__(self, target):
        object.__setattr__(self, "_target", target)

    def _set(self, target):
        object.__setattr__(self, "_target", target)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_target"), name)


class Connection:
    def __init__(self):
        env_real = os.environ.get("MAVLINK_URL") or (None if DEV else "/dev/ttyAMA0")
        env_sim = os.environ.get("SIM_MAVLINK_URL")
        default_mode = "sim" if os.environ.get("CONSOLE_SIM") == "1" else "real"
        saved = {}
        try:
            saved = json.loads(FILE.read_text())
        except (OSError, ValueError):
            pass
        self.urls = {"real": saved.get("real_url", env_real), "sim": saved.get("sim_url", env_sim)}
        self.mode = saved.get("mode") if saved.get("mode") in MODES else default_mode
        self.lock = threading.RLock()
        self.stores = {m: Store(DB[m]) for m in MODES}   # both open; only one is active
        self._link = None
        self.link = Proxy(None)
        self.store = Proxy(None)
        self.listeners = []          # called after every switch (controller reset, caches...)
        self._activate(start=False)

    # ---------------------------------------------------------------- state
    @property
    def sim(self):
        return self.mode == "sim"

    @property
    def url(self):
        return self.urls.get(self.mode)

    @property
    def enabled(self):
        return bool(self.url)

    def describe(self, kind=None):
        url = self.urls.get(kind or self.mode)
        if not url:
            return "not configured"
        return "serial port" if url.startswith("/dev/") else "network"

    def info(self, local=True):
        s = self.link.snapshot() if self.enabled and self._link else {}
        return {
            "mode": self.mode, "sim": self.sim, "enabled": self.enabled, "link": bool(s.get("link")),
            "armed": bool(s.get("armed")), "link_error": s.get("link_error"),
            "configured": {m: bool(self.urls.get(m)) for m in MODES},
            # Exact addresses are only shown on the local network.
            "urls": {m: (self.urls.get(m) if local else self.describe(m)) for m in MODES},
        }

    def save(self):
        FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"mode": self.mode, "real_url": self.urls["real"], "sim_url": self.urls["sim"]}))
        os.replace(tmp, FILE)

    # ---------------------------------------------------------------- switching
    def _activate(self, start=True):
        self.store._set(self.stores[self.mode])
        link = MavLink(self.url or "", MAVLINK_BAUD)
        self._link = link
        self.link._set(link)
        if start and self.enabled:
            link.start()

    def start(self):
        if self.enabled and self._link and not self._link.is_alive():
            self._link.start()

    def blocked_reason(self, busy):
        if busy:
            return "A mission is in progress. Wait for it to land."
        if self.enabled and self._link and self._link.snapshot().get("armed"):
            return "The drone is armed. Disarm it before switching."
        return None

    def switch(self, mode, busy=False):
        if mode not in MODES:
            raise ValueError("Mode must be 'sim' or 'real'.")
        with self.lock:
            why = self.blocked_reason(busy)
            if why:
                raise RuntimeError(why)
            if mode == self.mode:
                return self.info()
            if self._link:
                self._link.stop()
            self.mode = mode
            self.save()
            self._activate()
        for fn in self.listeners:
            fn()
        return self.info()

    def set_url(self, mode, url, busy=False):
        if mode not in MODES or not valid_url(url):
            raise ValueError("Use tcp:HOST:PORT, udp:HOST:PORT or a serial device such as /dev/ttyAMA0.")
        if self.urls.get(mode) == (url or None):
            return self.info()
        with self.lock:
            if mode == self.mode:
                why = self.blocked_reason(busy)
                if why:
                    raise RuntimeError(why)
            self.urls[mode] = url or None
            self.save()
            if mode == self.mode:   # reconnect to the new address
                if self._link:
                    self._link.stop()
                self._activate()
        if mode == self.mode:
            for fn in self.listeners:
                fn()
        return self.info()
