# Farm Command Center

The farmer dashboard served by the Pi at `http://<pi>:8000/`. The original live console is still at `/console`.

Code lives in `raspberry_pi/dashboard/`:

- **Backend modules**
  - `config.py`: environment settings.
  - `store.py`: SQLite database.
  - `geo.py`: field geometry.
  - `planner.py`: survey routes.
  - `mavlink_link.py`: flight-controller link and commands.
  - `preflight.py`: launch checks and tokens.
  - `missions.py`: mission state machine and capture loop.
  - `analytics.py`: derived figures.
  - `devices.py`: camera, AI, sensors and Pi health, moved unchanged from the old `app.py`.
  - `app.py`: routes.
- **Frontend**
  - `templates/farm.html`
  - `static/farm/`: vanilla ES modules, no build step.
  - `static/vendor/leaflet/`: Leaflet 1.9.4, served locally so the map works offline. Only the background imagery needs internet.

## Pages

| Page | What it shows |
|---|---|
| Overview | Farm area, fields, crop health, problems, captures, last inspection, alerts, recent detections, live status (drone link, armed, battery, GPS, mission, camera, Pi, sensors, telemetry age), field sensors |
| Operations | Camera with leaf boxes; live map (drone, home, planned route, flown track). **Mission panel:** field → route preview with time/battery estimate → create & upload (read-back verified) → pre-flight checks → start (confirmation + safety checkbox) → stage / progress / ETA. **Flight controls:** Pause, Resume, Return to launch, Land now. **Logs:** command and event log with acks, live detections |
| Farm map | Satellite/street base, draw / rename / redraw / archive fields, inspected area (latest flight), flight tracks, detection markers (colour **and** shape per type), attention clusters, filters (type, condition, period, confidence, uncertain). **Popup:** photo, confidence, position ± accuracy, time, field, mission, guidance |
| Disease log | Search, filters, sortable columns, paging, detail drawer (photo, per-leaf results, guidance, review: confirm / dismiss + note), show on map, CSV export of the filtered set |
| Analytics | Health over time, healthy vs problem leaves, problems per day, distribution, per-field table, change since previous inspection, condition × field heatmap, attention areas, flight statistics |
| Missions | Every mission. **Detail page:** route vs flown track, inspected cells, stats, plan, full event log, findings, findings CSV |
| System | Flight controller, GPS/EKF/home, Pi health, camera + model card, data, autopilot messages, settings (farm name, endurance, min battery, camera FOV, defaults, GPS thresholds) |

Dark theme by default; light theme via the toggle (remembered per browser). A striped **SIMULATOR** banner shows when the backend is on SITL. A **VIEW-ONLY** banner shows on the public link.

## Simulator or real drone

Mission control talks to one of two flight controllers. You switch with the **Simulator / Real drone** control on Operations or System, or with the mode chip in the top bar.

| | Simulator | Real drone |
|---|---|---|
| Address (System → Connection) | ArduPilot SITL, e.g. `tcp:farm-sitl:5760` (devstack) or `tcp:<Mac IP>:5763` from the Pi | `/dev/ttyAMA0` on the Pi (Pixhawk TELEM2, 57600 baud) |
| Records | `captures/farm-sim.db` | `captures/farm.db` |
| Autopilot safety configuration (`ARMING_CHECK`, `FS_THR_ENABLE`, `BATT_FS_LOW_ACT`, `FENCE_ENABLE`) | advisory | **blocks launch** until set |
| UI | striped SIMULATOR banner, blue chip | amber "Real drone" chip, red "REAL DRONE" line in the start dialog |

- **The workflow is the same in both modes:** plan → upload + read-back verify → pre-flight checks → confirm → start → pause / resume / return / land.
- **When switching is allowed:** only from the local network, never while a mission is active or the drone is armed. Switching to real needs a confirmation checkbox. The choice survives restarts (`captures/connection.json`).
- **Simulator flights from the Pi:** run `devstack/up.sh` on the Mac, which publishes SITL's spare port 5763, then set the Pi's simulator address to `tcp:<Mac IP>:5763`.
- **Real drone before its first autonomous flight:** in Mission Planner, set `ARMING_CHECK=1`, `FS_THR_ENABLE=1`, `BATT_FS_LOW_ACT=2` with `BATT_LOW_VOLT`, and `FENCE_ENABLE=1` with `FENCE_RADIUS` and `FENCE_ALT_MAX`. As found, the drone has these off, so the console will refuse to launch until they are set.

## Safety model (what the code guarantees)

1. **Commands only from the local network.** Every changing endpoint is `@local_only`: fields, missions, upload, start, pause/resume/RTL/land, settings and detection review. It rejects any request carrying `CF-Connecting-IP` (the Cloudflare tunnel) or `X-Forwarded-For`, and any non-private address (HTTP 403). The public link is view-only. The UI disables the controls and explains why.
2. **Nothing is reported as done unless the autopilot said so.**
   - `command()` waits for the matching `COMMAND_ACK` and returns accepted / denied / unsupported / failed / **timeout**.
   - A mode change is only "confirmed" when a later HEARTBEAT shows the new mode.
   - Arming is confirmed by the HEARTBEAT armed flag.
   - Mission upload uses the full MAVLink mission protocol, then **reads the mission back and compares it** to the plan.
3. **Launch gating.** `GET /api/missions/<id>/preflight` runs the checks. Only when every critical check passes does it return a **single-use launch token valid for 60 s**. `POST …/start` needs `{"confirm": true, "token": …}`, and it **re-runs every check server-side** before sending anything.
   - **Critical checks:** local network, field, heartbeat < 3 s, disarmed, autopilot pre-arm OK, GPS 3D fix + satellites + HDOP limits, EKF absolute position, home set, launch within 1 km of the first waypoint (warning beyond 200 m), battery ≥ minimum, estimated flight ≤ 75 % of endurance, RC transmitter linked, mission uploaded and verified.
   - **Advisory checks:** geofence off, RTL altitude < 10 m (`RTL_ALT_M` on Copter 4.7, `RTL_ALT` before), camera offline.
4. **Start sequence:** GUIDED → arm (normal, **never forced**) → AUTO → MISSION_START. Each step waits for its ack and telemetry. The first failure stops the sequence; if the drone is armed on the ground, it is disarmed (normal disarm) and the reason is logged.
5. **In-flight commands** need a confirmation dialog in the UI and `{"confirm": true}` in the API:
   - **Pause** = BRAKE (LOITER if refused).
   - **Resume** = AUTO; only allowed from a pause.
   - **Return to launch** = RTL.
   - **Land now** = LAND.
   - There is **no motor-kill / flight-termination** button. Nothing is ever retried automatically.
6. **The stage comes from telemetry**, never from what was sent: mode, armed, `MISSION_CURRENT`, altitude.
   - A mode change made with the transmitter shows as "Pilot in control".
   - Loss of telemetry is shown; the autopilot's own failsafes continue.
   - If the console restarts mid-flight, monitoring resumes from the database, using checkpoints saved every 5 s.
7. Every command attempt and its result is stored in `mission_events`: who, when, what, and the ack.

## Honest numbers

- **Crop health %** = healthy ÷ (healthy + disease/deficiency/pest) leaves classified **confidently**, from the latest inspection of each field. Uncertain and "no leaf" are excluded. Below 20 leaves it shows "Not enough data". With no inspections yet, it falls back to the last 7 days of snapshots, and the basis is shown.
- **Inspected %** = share of the field's 5 m cells that came within half a camera swath of the **flown** track. Only points at ≥ 60 % of the planned altitude count. It is an estimate; the camera field of view used is a setting and is shown.
- **Detection position** = the drone's GPS at capture time, not the exact leaf. It is only attached when telemetry is < 1 s old and the fix is 3D. Accuracy comes from `GPS_RAW_INT.h_acc`, else HDOP × 2.5 m. Captures without a valid position are kept but not mapped.
- **Severity** is "Not assessed": the model does not estimate it.
- **Guidance** is a static, general table labelled "confirm with an agronomist".
- **Time and battery estimates** come from route length / speed, climb and descent, a turn penalty and the endurance setting. The assumptions are listed next to every plan.

## API

| Method | Path | Notes |
|---|---|---|
| GET | `/events` | SSE every 0.5 s: telemetry, camera, AI, env, system, `mission` (active stage, progress, ETA, last detection, recent commands), `sim`, `local` |
| GET | `/api/state` `/api/overview` `/api/system` `/api/analytics?days=` `/api/map?kind&label&from&min_p` | read-only |
| GET/POST | `/api/fields` | POST `{name, polygon:[[lat,lon],…], crop?, notes?}` (local) |
| PATCH/DELETE | `/api/fields/<id>` | DELETE archives instead if the field has history (local) |
| POST | `/api/missions/plan` | preview `{field_id, altitude_m, speed_ms}` |
| POST | `/api/missions` | create (local) |
| GET/DELETE | `/api/missions`, `/api/missions/<id>` | detail has events, track, detections, health, coverage; delete only never-flown missions (local) |
| POST | `/api/missions/<id>/upload` | upload + read-back verify (local) |
| GET | `/api/missions/<id>/preflight` | checks + token (token only when local and ready) |
| POST | `/api/missions/<id>/start` | `{confirm:true, token}` (local) |
| POST | `/api/mission/{pause,resume,rtl,land}` | `{confirm:true}` → CommandResult (local) |
| GET | `/api/mission/events?mission=` | command/event log |
| GET | `/api/log?kind&label&field&mission&from&to&review&q&min_p&sort&order&limit&offset` | disease log page |
| GET/PATCH | `/api/detections/<id>` | PATCH `{review_status: new\|confirmed\|dismissed, note}` (local) |
| GET | `/api/detections.csv?…` | same filters as `/api/log` |
| GET/PUT | `/api/settings` | PUT validated ranges (local) |
| Unchanged | `/console`, `/stream.mjpg`, `/frame.jpg` (new), `/api/snapshot`, `/api/sensors`, `/api/detections` (old list format), `/captures/<file>` | |

**Data:** `raspberry_pi/dashboard/captures/farm.db` (SQLite WAL, `synchronous=FULL`). The old `detections.jsonl` is imported once and kept; snapshots are still appended to it.

## Developer stack (Mac, simulator)

```bash
docker build -t ardupilot-sitl:4.7.1 ../drone-sim     # once, ~20 min
devstack/up.sh                                         # SITL + console on http://localhost:8090
python3 tests/sitl_mission.py            # launch → survey → pause/resume → RTL → landed (34 checks)
python3 tests/sitl_mission.py --full     # complete survey, 100 % coverage
docker exec -w /app farm-console python3 tests/sitl_failures.py    # low battery, GPS loss, unsupported command, tunnel
python3 tests/sitl_connection.py         # simulator <-> real switching, separate records, real-mode safety gates
docker exec -w /app farm-console python3 -m pytest tests/test_dashboard.py -q   # 43 unit tests
devstack/down.sh
```

The dev data lives in `devstack/devdata/` and never touches the Pi. The fake camera replays `devstack/images/` (pages from `demo/print_set.pdf`).

## Deploying to the Pi (not done yet: the Pi was offline)

```bash
rsync -av --exclude captures --exclude __pycache__ raspberry_pi/ drones@raspberrypi.local:~/disease-detection/raspberry_pi/
ssh drones@raspberrypi.local 'sudo systemctl restart crop-console && sleep 8 && curl -s localhost:8000/api/system | head -c 400'
```

**Expected result indoors:** telemetry live, pre-flight **blocked** (no GPS / EKF / home), and the launch button disabled. That is correct.

Don't start a mission indoors. The Pi's existing `detections.jsonl` is imported into `farm.db` on first start.

## What is still missing before a real autonomous flight

- **Pi flight power:** a 5 V 5 A UBEC from the flight battery. Today the Pi has no power in the air, so no captures.
- **A downward (nadir) camera mount.** The camera faces sideways; coverage and positions assume straight down.
- **Measured camera field of view and real usable flight time.** Enter them in System → Settings.
- **Outdoor GPS lock.** Enable the geofence (`FENCE_ENABLE=1`) and assign an RTL/mode switch on the transmitter.
- **Permission** to fly autonomously (GCAA), and a pilot with the transmitter at every flight.
- **BME680 / soil nodes:** none exist yet. The sensor cards show an empty state.
- **Severity:** needs a lesion-area model.
