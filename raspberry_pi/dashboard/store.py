"""SQLite storage for fields, missions, flight tracks, detections and settings.

WAL + synchronous=FULL: the Pi has lost power mid-write before, so every commit is durable.
All access goes through one connection guarded by a lock (Flask runs threaded).
"""
import json
import sqlite3
import threading
import time
import uuid

from geo import area_m2

SCHEMA = """
CREATE TABLE IF NOT EXISTS fields (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, crop TEXT NOT NULL DEFAULT 'tomato',
  polygon TEXT NOT NULL, area_m2 REAL NOT NULL, notes TEXT, archived INTEGER NOT NULL DEFAULT 0,
  created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS missions (
  id TEXT PRIMARY KEY, field_id TEXT REFERENCES fields(id), status TEXT NOT NULL,
  plan TEXT NOT NULL, created REAL NOT NULL, uploaded REAL, started REAL, ended REAL,
  sim INTEGER NOT NULL DEFAULT 0, stats TEXT, end_reason TEXT);
CREATE TABLE IF NOT EXISTS mission_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, mission_id TEXT, t REAL NOT NULL,
  level TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL, data TEXT);
CREATE TABLE IF NOT EXISTS mission_track (
  mission_id TEXT NOT NULL, t REAL NOT NULL, lat REAL, lon REAL, alt REAL, gs REAL, hdg REAL);
CREATE TABLE IF NOT EXISTS detections (
  id TEXT PRIMARY KEY, t REAL NOT NULL, source TEXT NOT NULL, mission_id TEXT, field_id TEXT,
  lat REAL, lon REAL, alt REAL, gps_fix INTEGER, hdop REAL, h_acc_m REAL, telemetry_age_ms INTEGER,
  label TEXT NOT NULL, kind TEXT NOT NULL, p REAL, status TEXT, top TEXT, leaves TEXT,
  n_leaves INTEGER NOT NULL DEFAULT 0, n_healthy INTEGER NOT NULL DEFAULT 0,
  n_problem INTEGER NOT NULL DEFAULT 0, n_uncertain INTEGER NOT NULL DEFAULT 0,
  image TEXT, thumb TEXT, model TEXT, truth TEXT, test_item TEXT, mode TEXT,
  review_status TEXT NOT NULL DEFAULT 'new', review_note TEXT);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS det_t ON detections(t);
CREATE INDEX IF NOT EXISTS det_mission ON detections(mission_id);
CREATE INDEX IF NOT EXISTS track_mission ON mission_track(mission_id, t);
CREATE INDEX IF NOT EXISTS events_mission ON mission_events(mission_id, t);
"""

DEFAULT_SETTINGS = {
    "farm_name": "My farm",
    "endurance_min": 8.0,        # usable flight time of the 3S 4200 mAh pack on the F450 (assumption)
    "min_battery_v": 11.4,       # 3S: ~3.8 V/cell resting before take-off
    "camera_hfov_deg": 70.0,     # e-CAM130 lens field of view (assumption until measured)
    "default_altitude_m": 12.0,
    "default_speed_ms": 3.0,
    "sidelap": 0.25,
    "max_hdop": 2.0,
    "min_sats": 8,
}

PROBLEM_KINDS = ("disease", "deficiency", "pest")


def kind_of(label):
    if label == "no_tomato_leaf":
        return "none"
    if "healthy" in label.lower():
        return "healthy"
    if "deficiency" in label:
        return "deficiency"
    if label in ("leaf_miner", "spider_mites"):
        return "pest"
    return "disease"


def leaf_counts(leaves):
    """(n_leaves, n_healthy, n_problem, n_uncertain) from per-leaf classifications."""
    n = h = pr = u = 0
    for leaf in leaves or []:
        if leaf.get("label") == "no_tomato_leaf":
            continue
        n += 1
        if leaf.get("status") == "uncertain":
            u += 1
        elif kind_of(leaf["label"]) == "healthy":
            h += 1
        else:
            pr += 1
    return n, h, pr, u


def new_id(prefix):
    return f"{prefix}-{time.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"


class Store:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.executescript(SCHEMA)

    # ---------------------------------------------------------------- helpers
    def q(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        rows = self.q(sql, args)
        return rows[0] if rows else None

    def x(self, sql, args=()):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                cur = self.db.execute(sql, args)
                self.db.execute("COMMIT")
                return cur.rowcount
            except Exception:
                self.db.execute("ROLLBACK")
                raise

    def xmany(self, sql, rows):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                self.db.executemany(sql, rows)
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise

    # ---------------------------------------------------------------- settings
    def settings(self):
        out = dict(DEFAULT_SETTINGS)
        for r in self.q("SELECT key, value FROM settings"):
            out[r["key"]] = json.loads(r["value"])
        return out

    def set_settings(self, changes):
        rows = [(k, json.dumps(v)) for k, v in changes.items()]
        self.xmany("INSERT INTO settings(key, value) VALUES(?, ?) "
                   "ON CONFLICT(key) DO UPDATE SET value=excluded.value", rows)

    # ---------------------------------------------------------------- fields
    @staticmethod
    def _field(r):
        if r:
            r["polygon"] = json.loads(r["polygon"])
            r["archived"] = bool(r["archived"])
        return r

    def fields(self, include_archived=False):
        sql = "SELECT * FROM fields" + ("" if include_archived else " WHERE archived=0") + " ORDER BY created"
        return [self._field(r) for r in self.q(sql)]

    def field(self, fid):
        return self._field(self.one("SELECT * FROM fields WHERE id=?", (fid,)))

    def add_field(self, name, polygon, crop="tomato", notes=None):
        fid, t = new_id("F"), time.time()
        self.x("INSERT INTO fields(id, name, crop, polygon, area_m2, notes, created, updated) "
               "VALUES(?,?,?,?,?,?,?,?)", (fid, name, crop, json.dumps(polygon), area_m2(polygon), notes, t, t))
        return self.field(fid)

    def update_field(self, fid, **kw):
        sets, args = [], []
        for k in ("name", "crop", "notes"):
            if k in kw:
                sets.append(f"{k}=?")
                args.append(kw[k])
        if "polygon" in kw:
            sets += ["polygon=?", "area_m2=?"]
            args += [json.dumps(kw["polygon"]), area_m2(kw["polygon"])]
        if "archived" in kw:
            sets.append("archived=?")
            args.append(1 if kw["archived"] else 0)
        if sets:
            self.x(f"UPDATE fields SET {', '.join(sets)}, updated=? WHERE id=?", (*args, time.time(), fid))
        return self.field(fid)

    def delete_field(self, fid):
        return self.x("DELETE FROM fields WHERE id=?", (fid,))

    def field_has_history(self, fid):
        return bool(self.one("SELECT 1 FROM missions WHERE field_id=? UNION SELECT 1 FROM detections "
                             "WHERE field_id=? LIMIT 1", (fid, fid)))

    # ---------------------------------------------------------------- missions
    @staticmethod
    def _mission(r):
        if r:
            r["plan"] = json.loads(r["plan"])
            r["stats"] = json.loads(r["stats"]) if r["stats"] else None
            r["sim"] = bool(r["sim"])
        return r

    def add_mission(self, field_id, plan, sim):
        mid = new_id("M")
        self.x("INSERT INTO missions(id, field_id, status, plan, created, sim) VALUES(?,?,?,?,?,?)",
               (mid, field_id, "planned", json.dumps(plan), time.time(), 1 if sim else 0))
        return self.mission(mid)

    def mission(self, mid):
        return self._mission(self.one("SELECT * FROM missions WHERE id=?", (mid,)))

    def missions(self, limit=200, field_id=None):
        if field_id:
            rows = self.q("SELECT * FROM missions WHERE field_id=? ORDER BY created DESC LIMIT ?", (field_id, limit))
        else:
            rows = self.q("SELECT * FROM missions ORDER BY created DESC LIMIT ?", (limit,))
        return [self._mission(r) for r in rows]

    def update_mission(self, mid, **kw):
        sets, args = [], []
        for k, v in kw.items():
            sets.append(f"{k}=?")
            args.append(json.dumps(v) if k in ("stats", "plan") else v)
        self.x(f"UPDATE missions SET {', '.join(sets)} WHERE id=?", (*args, mid))
        return self.mission(mid)

    def event(self, mission_id, level, kind, text, data=None):
        self.x("INSERT INTO mission_events(mission_id, t, level, kind, text, data) VALUES(?,?,?,?,?,?)",
               (mission_id, time.time(), level, kind, text, json.dumps(data) if data is not None else None))

    def events(self, mission_id=None, limit=200):
        if mission_id:
            rows = self.q("SELECT * FROM mission_events WHERE mission_id=? ORDER BY id DESC LIMIT ?", (mission_id, limit))
        else:
            rows = self.q("SELECT * FROM mission_events ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            r["data"] = json.loads(r["data"]) if r["data"] else None
        return rows

    def add_track(self, rows):
        self.xmany("INSERT INTO mission_track(mission_id, t, lat, lon, alt, gs, hdg) VALUES(?,?,?,?,?,?,?)", rows)

    def track(self, mission_id):
        return self.q("SELECT t, lat, lon, alt, gs, hdg FROM mission_track WHERE mission_id=? ORDER BY t", (mission_id,))

    # ---------------------------------------------------------------- detections
    @staticmethod
    def _det(r):
        if r:
            for k in ("top", "leaves"):
                r[k] = json.loads(r[k]) if r[k] else []
        return r

    def add_detection(self, d):
        leaves = d.get("leaves") or []
        n, h, pr, u = leaf_counts(leaves)
        row = {
            "id": d.get("id") or new_id("D"), "t": d["t"], "source": d.get("source", "manual"),
            "mission_id": d.get("mission_id"), "field_id": d.get("field_id"),
            "lat": d.get("lat"), "lon": d.get("lon"), "alt": d.get("alt"), "gps_fix": d.get("gps_fix"),
            "hdop": d.get("hdop"), "h_acc_m": d.get("h_acc_m"), "telemetry_age_ms": d.get("telemetry_age_ms"),
            "label": d["label"], "kind": kind_of(d["label"]), "p": d.get("p"), "status": d.get("status"),
            "top": json.dumps(d.get("top") or []), "leaves": json.dumps(leaves),
            "n_leaves": n, "n_healthy": h, "n_problem": pr, "n_uncertain": u,
            "image": d.get("image"), "thumb": d.get("thumb"), "model": d.get("model"),
            "truth": d.get("truth"), "test_item": d.get("test_item"), "mode": d.get("mode"),
        }
        cols = ", ".join(row)
        self.x(f"INSERT OR IGNORE INTO detections({cols}) VALUES({', '.join('?' * len(row))})", tuple(row.values()))
        return self.detection(row["id"])

    def detection(self, did):
        return self._det(self.one("SELECT * FROM detections WHERE id=?", (did,)))

    def detections(self, kind=None, label=None, field_id=None, mission_id=None, t_from=None, t_to=None,
                   review=None, q=None, min_p=None, mapped_only=False, sort="t", order="desc",
                   limit=500, offset=0):
        where, args = [], []
        for col, val in (("kind", kind), ("label", label), ("field_id", field_id),
                         ("mission_id", mission_id), ("review_status", review)):
            if val:
                where.append(f"{col}=?")
                args.append(val)
        if t_from is not None:
            where.append("t>=?")
            args.append(t_from)
        if t_to is not None:
            where.append("t<?")
            args.append(t_to)
        if min_p is not None:
            where.append("p>=?")
            args.append(min_p)
        if mapped_only:
            where.append("lat IS NOT NULL AND lon IS NOT NULL")
        if q:
            where.append("(id LIKE ? OR label LIKE ? OR mission_id LIKE ? OR review_note LIKE ?)")
            args += [f"%{q}%"] * 4
        sort_col = {"t": "t", "p": "p", "label": "label"}.get(sort, "t")
        direction = "ASC" if order == "asc" else "DESC"
        sql = "SELECT * FROM detections" + (" WHERE " + " AND ".join(where) if where else "")
        total = self.one(sql.replace("SELECT *", "SELECT COUNT(*) AS n", 1), args)["n"]
        rows = self.q(f"{sql} ORDER BY {sort_col} {direction} LIMIT ? OFFSET ?", (*args, limit, offset))
        return total, [self._det(r) for r in rows]

    def review_detection(self, did, status, note=None):
        self.x("UPDATE detections SET review_status=?, review_note=? WHERE id=?", (status, note, did))
        return self.detection(did)

    def import_jsonl(self, path):
        """One-time import of the legacy console log (kept on disk; existing ids are skipped)."""
        if not path.exists() or self.one("SELECT value FROM settings WHERE key='legacy_imported'"):
            return 0
        count = 0
        for line in path.read_text().splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if not e.get("label"):
                continue
            self.add_detection({**e, "source": "manual", "alt": e.get("alt_rel"),
                                "gps_fix": 3 if e.get("lat") is not None else None})
            count += 1
        self.set_settings({"legacy_imported": time.time()})
        return count
