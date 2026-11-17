# HANDOVER: AI Crop-Disease Drone Project (complete context)

> This is written for the next agent or engineer. It covers everything done from 2026-10-08 to 2026-11-01, the current state, every file, every gotcha, and what's still open.
>
> **No secrets are written here.** Passwords and keys live only on the devices. Live state, such as which services are running, may have changed since 2026-11-01, so **verify before acting**.

---

## 0. TL;DR

- **The project:** a university team's (AUS, UAE) AI agricultural drone.
  - It flies over a field and photographs crops.
  - An on-board AI detects leaf disease, and each finding is geotagged.
  - It's planned to combine LoRa ground sensors (BME680 + soil moisture) with a web dashboard.
  - **The crop currently modelled is TOMATO**; the brief originally said wheat.
- **The hardware was inherited** from a previous student team, undocumented. It has been audited, made safe, and its firmware updated.
- **Built so far:**
  1. A **live mission console**: Flask on the Pi, password-protected, optionally public via a Cloudflare tunnel.
  2. A **new disease model (v2)**:
     - trained on a rented Vast.ai GPU;
     - runs on the Pi as a **YOLO11n leaf detector → MobileNetV3-L per-leaf classifier**;
     - results on held-out field photos: **81.6 % accuracy, macro-F1 0.807, 15.6 % false-disease on non-tomato images**, against the old model's 37.9 %, 0.23 and 99 %.
  3. A **sensor risk module** using published plant-pathology rules, with fusion.
  4. Runbook, audit report and demo print set.
- **State at last check:**
  - **2026-11-01:** the Pi had just rebooted, and the console and tunnel were **not running**, because they're transient units. **v2.0 is deployed** on the Pi.
  - **2026-11-06:** the Vast instance was **unreachable** (connection refused), so it's **stopped or destroyed**. A *stopped* instance keeps its disk; a *destroyed* one loses it. The better **v2.1** is complete **only on Vast**, and its Mac copy is incomplete (§7). Restart the instance from the Vast dashboard to recover it; the SSH port may change.

---

## 1. People, constraints, preferences

- **User:** Khalid (GitHub `khalid2277`), an AUS student. Use they/them pronouns for others.
- **The team repo is PRIVATE:** `github.com/jamila96422/disease-detection`, owned by a teammate, Jamila.
  - The Mac has the GitHub credentials; **the Pi cannot clone it**, so copy from the Mac.
  - **Nothing has been pushed** to that repo. All work is local and uncommitted. Ask before pushing or opening a PR.
- **The user's working style and constraints:**
  - Wants the best possible model, and honesty about results.
  - Wants step-by-step guidance for physical tasks.
  - Earlier asked for "understand before changing" on the inherited system. Later explicitly authorised the safety-parameter changes, the firmware reflash and the training work.
  - **Cares about Vast GPU credits.** Agreed rule: *promote any model change only if it beats the current one on the VALIDATION set*. The held-out test is reported, never used for selection.
  - Wants **distinctive, non-generic UI** (see memory `avoid-ai-slop-frontends`). They asked for professional, elegant, light mode, and for the `/ui-ux-pro-max` skill to be used.
- **Safety and permission limits that apply to an agent:**
  - Never type passwords.
  - Motor commands are only allowed via the capped script `/home/drones/motor_test.py`. The user added the allow rule `Bash(ssh drones@raspberrypi.local python3 /home/drones/motor_test.py:*)` in `~/.claude/settings.json`.
  - Enabling the boot-time public exposure (systemd enable) was **blocked by the classifier**, so the user has to run it themselves (see the runbook).
- **Security debts:**
  1. The Pi login password was shared in plain chat on 2026-10-08. It's weak and should be changed.
  2. The console password the user set is 5 characters long; recommend 12+.
  3. `~/.claude/settings.json` contains plaintext Supabase and Cloudflare R2 secrets in old `allow` entries from another project. Tell the user to rotate them. **Do not print them.**

---

## 2. Hardware (as found and as modified)

| Component | Details |
|---|---|
| Frame | HAWK'S WORK F450 quad, Quad-X |
| Motors / props | 4× A2212 920KV; 10×4.5 props. **Verified 2026-10-09:** A front-right CCW, B back-right CW, C back-left CCW, D front-left CW. CW props ("R") go on B and D |
| Flight controller | Pixhawk 2.4.8 (board target **Pixhawk1**, 2 MB flash). **Reflashed to ArduCopter 4.7.1** via QGroundControl on 2026-10-09; it was 4.6.2 with a CrashDump record |
| GPS / compass | u-blox M8N. Compass 1 IST8310 (I2C0, priority 1, calibration "green"); compass 2 HMC5883 (I2C1, "yellow"). Calibrated 2026-10-09 |
| Radio | Flysky FS-i6/i6X transmitter (4×AA, replaced 2026-10-09) + FS-iA10B receiver, 8 channels. **Mode switch = the 3-position switch, CH5:** up 1000 = STABILIZE, middle/down = ALT_HOLD. CH7 has `RC7_OPTION=4` (RTL) but **no transmitter switch is assigned**. CH6/CH8 are unused |
| Power module | 5.45 V / 2.5 A max, 2–12S, XT60 in/out, 6-pin cable to the Pixhawk POWER port. **Powers only the Pixhawk.** A loose 6-pin cable once made the voltage read 4–9 V (actual 12.5 V) and triggered a false battery failsafe; reseating it fixed this |
| Battery | 3S LiPo, 4200 mAh (`BATT_CAPACITY`). Charger: ToolkitRC M4AC; it starts automatically once both leads are connected correctly; balance plug cells must show as 1–3 |
| Companion computer | Raspberry Pi 4B Rev 1.5, 8 GB RAM, 58 GB SD (~24 % used), in a fan case |
| Camera | e-con **e-CAM130_CURB (AR1335 13 MP)** on CSI-2. **Faces sideways**; it needs a downward mount for mapping |
| Pi ↔ Pixhawk link | Pi GPIO14/15 UART (`/dev/ttyAMA0`, PL011, Bluetooth disabled) ↔ Pixhawk TELEM2 (SERIAL2, MAVLink2, 57600). The Pixhawk is also powered from the Pi via the TELEM2 5 V wire when the LiPo is off. The link was **once one-way intermittent** (Pi→Pixhawk not received); it recovered on its own. USB is a robust alternative |
| **Pi power on the drone** | **NONE.** The GPIO red/black wires are the case fan. With only the LiPo connected, the Pi's LED lit (back-feed through the UART lines) but it didn't boot. The previous team likely used a USB-C power bank. **Needed: a 5 V 5 A UBEC → USB-C.** There were several power-loss reboots on 2026-10-31, which zeroed freshly written files; always `sync` |

---

## 3. Networks and access

| Network | Notes |
|---|---|
| **AL FAHIM** (home ASUS router, 192.168.1.x) | The Pi gets **192.168.1.49**; autoconnect priority **10** (preferred) |
| **K’s iPhone** hotspot (172.20.10.x) | The Pi gets **172.20.10.2**; priority **5**. The SSID has a **curly apostrophe**, so connect by BSSID `82:EB:4C:06:65:D6` if needed. Keep the iPhone hotspot screen open, with Maximize Compatibility on |
| **HAMAD** (TP-Link Archer C6, 192.168.20.x) | Pi .191 (Wi-Fi) / .190 (Ethernet); priority 0 |
| Others saved | AUS_Wireless, preconfigured (AUS_Guest, hidden), Turki_iPhone; old campus profiles |

- **SSH:** `ssh drones@raspberrypi.local`, using the Mac's ed25519 key at `~/.ssh/id_ed25519` (created 2026-10-08, no passphrase), which is installed in the Pi's `authorized_keys`.
  - Host keys for `raspberrypi.local`, `192.168.20.190` and `192.168.20.191` are in the Mac's `known_hosts`.
  - **`172.20.10.2` is not**: a raw-IP SSH fails host-key verification, so use `raspberrypi.local`.
- The original Wi-Fi failure was the **regulatory domain not being set**; fixed with `raspi-config nonint do_wifi_country AE`.
- `rfkill` and `iw` aren't installed on the Pi.
- **Vast GPU:** `ssh -p 12059 root@ssh3.vast.ai`.
  - RTX PRO 6000 Blackwell, 96 GB; 128 cores; 188 GB RAM; 80 GB disk.
  - Storage is **not persistent** (`workspace_is_volume: false`).
  - Its `llama` supervisor service holds about **32 GB of VRAM**. Don't touch it.
  - There's an older, different instance in memory notes (`vast-qwen-codex`); don't confuse the two.

---

## 4. Raspberry Pi software state

- **OS:** Raspberry Pi OS Bookworm 64-bit desktop (2025-05-13 image), Debian 12.
- **Upgraded 2026-10-08** with `apt full-upgrade`: 432 packages, nothing removed. Log: `~/backups/upgrade-2026-10-08.log`.
- **Kernel: custom e-con build `6.12.47-v8+`**, which is required for the camera driver `ar1335_mcu.ko` (the `ar1335_mcu.dtbo` overlay).
  - **Never upgrade the kernel.** These packages are on hold: `linux-image-rpi-v8`, `linux-image-rpi-2712`, `linux-headers-rpi-v8`, `linux-headers-rpi-2712`, `raspi-firmware`. The old team's legacy holds also remain: `raspberrypi-kernel`, `raspberrypi-bootloader`.
  - **Backup:** `~/backups/boot-firmware-2026-10-08/`, with hashes in `~/backups/econ-camera-sha256.txt`. Check with `sha256sum -c`.
- **Python:** system 3.11. OpenCV **4.13-dev built from source** (`/usr/local`, with GStreamer), **numpy pinned at 1.26.4**. Don't upgrade pip packages: numpy 2 breaks cv2 and TFLite.
  - Also present: torch 2.8, ultralytics 8.3 (in `~/.local`), MAVProxy 1.8.74, pymavlink 2.4.49, DroneKit 2.9.2 (abandoned upstream), tflite_runtime 2.14, Flask 2.2.2.
- **cloudflared 2026.10.7** was installed 2026-10-31 from the official GitHub `.deb`.
- **Camera recipe** (needed, or frames come back black):
  - find the `/dev/mediaN` of `unicam`, because **N changes between boots**;
  - `media-ctl -d /dev/mediaN --set-v4l2 '"ar1335_mcu 10-0042":0 [fmt:UYVY8_2X8/1920x1080]'`;
  - `v4l2-ctl -d /dev/video0 --set-fmt-video=width=1920,height=1080,pixelformat=UYVY`;
  - GStreamer `v4l2src io-mode=2 ! UYVY 1920x1080 ! videoconvert ! BGR appsink` gives about **20 fps**. 4K works too, with UYVY 3840x2160.
  - **Always shut the Pi down before re-seating the ribbon cable.** It was hot-plugged once.
- **The previous team's work** (kept, unchanged):
  - `~/Mission/Mission.py`: SSD-MobileNet person detection with GPS tagging, which uploads to a campus laptop.
  - `~/yolo`, `~/opencv` (source tree, 821 MB).
  - Field-test captures in `~/Desktop/*`.
  - **Mission data stored in `~/.local/share/Trash/files`, on purpose. Don't empty the Trash.**
- **Services:** nothing drone-related autostarts. Our services are **transient `systemd-run` units, lost on reboot**: `crop-console` (port 8000) and `crop-tunnel`. Boot-unit files exist in the repo (see §7) but **aren't installed**.

---

## 5. Pixhawk / ArduPilot configuration (changes made)

- **Firmware:** reflashed 4.6.2 → **4.7.1** (Pixhawk1 target) via QGC to clear a "PreArm: CrashDump data detected" block. Parameters migrated: many were renamed in 4.7 (`ARMING_CHECK` → `ARMING_SKIPCHK`, `WPNAV_*` → `WP_*`, `RTL_ALT` → `RTL_ALT_M`, `SR*` → `MAV*`…). To flash, the Pixhawk must lose **all** power, including the Pi's 5 V via TELEM2, before it will enter the bootloader.
- **Safety, set via `drone-audit/set_safety_params.py` (2026-10-09):** `ARMING_CHECK` 0→1, which is now `ARMING_SKIPCHK=0`; `FS_THR_ENABLE` 0→1; `BATT_FS_LOW_ACT` 0→2 (RTL); `BATT_FS_CRT_ACT` 0→1 (Land).
- **Flight modes** via `set_params.py`: `FLTMODE1=0` (Stabilize), `FLTMODE4=2` (AltHold), `FLTMODE6=2` (AltHold). Earlier values were 0/5 (Loiter)/3 (Auto).
- **Not yet done** (proposed): geofence (`FENCE_ENABLE` is off), `RTL_ALT_M` 3 → 15, `LOIT_SPEED_MS` 12.5 → 5, an RTL switch on CH7, accelerometer and level calibration, and possibly disabling compass 2.
- **Calibration:** compass done 2026-10-09. Pre-arm then passed: battery OK, RC OK, GPS 3D fix with 4–5 satellites by a window.
- **Indoors:** GPS modes (Loiter, Auto, RTL, Guided) won't work. Autonomous indoor flight needs an **optical-flow + rangefinder sensor** (e.g. Matek 3901-L0X / MicoAir MTF-01) or lab motion capture. Outdoor flying in the UAE needs **GCAA registration** and a permitted area.
- **Incidents:**
  - An indoor arm **with props on** made the drone lift off. That flight is log #57 (2026-10-09 16:21 UTC, not analysed).
  - A false battery failsafe forced LAND mode; cleared via a MAV reboot.
  - Props-off arm tests showed I-term windup at low throttle.
  - All 4 motors were then verified.
- **Logs on the Pixhawk SD:** #57 (incident) and #58–60 (later tests, no GPS time). **None downloaded.** `drone-audit/log_download.py` works over MAVLink (slow at 57600), or use QGC Analyze → Log Download over USB.
- **Parameter backups:** `drone-audit/pixhawk-live.txt` (2026-10-08, 4.6.2, 1020 parameters) and `drone-audit/old-mav.parm` (the previous team's).

---

## 6. Live mission console (Pi, `raspberry_pi/dashboard/`)

> **Update 2026-11-17: Farm Command Center.** `/` is now the farmer dashboard, and this console moved to `/console`, unchanged. The new dashboard adds:
> - SQLite (`captures/farm.db`);
> - fields and the survey planner;
> - **autonomous inspection missions over MAVLink**, with acked commands, read-back verified upload, pre-flight gating plus a 60 s launch token, and local-network-only commands;
> - GPS disease map, disease log with CSV, analytics, mission history, system page; dark/light.
>
> The old `Telemetry` thread is replaced by `mavlink_link.MavLink`, which **does send commands now**, but only from mission/command endpoints after confirmation. Tested end-to-end against ArduCopter 4.7.1 SITL (`devstack/`, `tests/sitl_*.py`), never on the real drone. **Not yet deployed to the Pi** (it was offline). Everything is in **`docs/COMMAND_CENTER.md`**. Notes below describe the original console.

- **Run** (see `docs/RUNBOOK.md`): `python3 raspberry_pi/dashboard/app.py` from `~/disease-detection`, as the `crop-console` transient unit. Open `http://raspberrypi.local:8000`.
- **Auth:** HTTP Basic, any username, password from `~/.config/crop-console/password` (mode 600, set by the user). A **lockout** applies after 10 wrong attempts per client IP (uses `CF-Connecting-IP` behind the tunnel), for 15 minutes. If there's no password file, it's open.
- **Public link:** Cloudflare quick tunnel (`crop-tunnel`). **The URL changes on every start.** Get the current one with `sh ~/disease-detection/raspberry_pi/systemd/crop-link`; it's also shown in the console under "Companion computer". Remote viewers automatically get a 5 fps stream (about 400 MB/h) instead of 20 fps.
- **Backend threads:**
  - `Camera`: GStreamer, 1080p, discovers `/dev/mediaN` automatically;
  - `Analyzer`: v2 `LeafPipeline` if `ml/models/tomato-v2/model.json` exists, otherwise the baseline; every 1.5 s;
  - `Telemetry`: **read-only** MAVLink. It sends only a GCS heartbeat and stream-rate requests, never commands;
  - `SystemHealth`;
  - `SensorStore`.
- **Endpoints:** `/`, `/stream.mjpg?fps=N`, `/events` (SSE), `/api/state`, `/api/detections`, `POST /api/snapshot` (JSON `{truth, item}` for test mode), `POST/GET /api/sensors`, `/captures/<file>`.
- **Data:** `raspberry_pi/dashboard/captures/` holds `detections.jsonl`, snapshot JPGs and `sensors.jsonl`. Writes are fsync'd.
- **UI** ("field ledger" light theme):
  - Fraunces + IBM Plex Sans/Mono; paper, ink, field green, wheat accent; hairline rules; square tags; numbered sections 01–08.
  - Sections: live view with **per-leaf boxes**; AI diagnosis (headline, verdict kinds disease / nutrient deficiency / pest / healthy / no tomato leaf / uncertain, the environmental-boost note, per-leaf list, test-mode selector); flight telemetry (mode, armed, pre-arm, battery and altitude sparklines with pause, GPS, artificial horizon, links, autopilot messages); field map (Leaflet/OSM, CDN); detection log; field conditions (sensors); companion computer; the system pipeline A–E plus the model card.
  - Checked at desktop and 375 px. WCAG-minded: skip link, focus rings, 44 px targets, aria-live, reduced motion.
- **Dev mode** (for testing without the camera, serial or password): `CONSOLE_DEV=1 CONSOLE_PORT=8011 CONSOLE_DATA=/tmp/x [CONSOLE_DEV_IMAGES=<folder of jpg>]`. It binds localhost only; tunnel with `ssh -L 8011:127.0.0.1:8011`. Run dev builds from `~/console-dev`, **not** the live folder, because Flask serves templates and static files from disk.
- **The console holds `/dev/video0` and `/dev/ttyAMA0`.** Stop it before running `motor_test.py`, `set_params.py`, `log_download.py` or `infer.py --camera`.

---

## 7. Repository layout

The local copy is at **`~/Desktop/Alfatechlabs/Code/Drones & Hardware/disease-detection`**, mirrored to Pi `~/disease-detection`.

> On 2026-11-02 a Desktop reorganisation moved `~/Desktop/{disease-detection,drone-audit,drone-sim}` into `~/Desktop/Alfatechlabs/Code/Drones & Hardware/`. The move log is `~/Desktop/_To Review/move-log.json`. Older notes and commands that use `~/Desktop/disease-detection` refer to that old path.

```
ml/                       (team's original) train.py, export.py, evaluate.py, eval_roboflow.py, model.onnx (baseline), classes.json
ml/models/tomato-v2/      DEPLOYED v2.0 package: classifier.onnx, detector.onnx, model.json   (Mac + Pi)
ml/models/tomato-v2.1/    INCOMPLETE on the Mac: classifier.onnx present, detector.onnx truncated (1.8/10.6 MB), no model.json.
                          The complete v2.1 is on Vast at /workspace/agri/package/tomato-v2.1. The detector is identical
                          to tomato-v2/detector.onnx, so only classifier.onnx + model.json actually differ.
ml/v2/                    all new training code (see §8)
ml/v2/data/               download.sh, prepare.py, cache.py, det_prepare.py
raspberry_pi/infer.py     CLI: --image / --camera (auto camera setup), baseline model
raspberry_pi/pipeline.py  LeafPipeline: YOLO detect -> crops -> classifier, cv2.dnn only; calibrated temperature; 'uncertain'
raspberry_pi/env_risk.py  sensor disease-risk rules + boost-only fuse()
raspberry_pi/dashboard/   app.py, templates/index.html, static/{style.css,app.js}, captures/
raspberry_pi/systemd/     crop-console.service, crop-tunnel.service (refuses without password), crop-link
docs/RUNBOOK.md           every command to run the system without an agent
docs/HANDOVER.md          this file
demo/print_set.pdf        30 held-out photos (2 per class + 4 non-tomato plant negatives), IDs only
demo/answer_key.csv       ID -> true class (keep away from the camera)
```
Other locations:
- **Mac `~/Desktop/Alfatechlabs/Code/Drones & Hardware/drone-audit/`:**
  - `INHERITED-SYSTEM-AUDIT.md`: the full audit of the inherited system;
  - `pixhawk-live.txt`, `old-mav.parm`;
  - `motor_test.py` (also on the Pi at `/home/drones/motor_test.py`, capped at 10 % throttle and 3 s, refuses if armed; wait about 4 s between runs because the Pixhawk reports "armed" during a test);
  - `set_safety_params.py`, `set_params.py` (also on the Pi at `/home/drones/set_params.py`), `log_download.py` (also on the Pi).
- **Mac `~/Desktop/Alfatechlabs/Code/Drones & Hardware/drone-sim/`:** Dockerfile for **ArduCopter 4.7.1 SITL**. The image `ardupilot-sitl:4.7.1` was built in colima (TCP 5760; home at AUS 25.3094, 55.4861). Planned for testing an autonomous take-off / hold / land script; **no script written yet**.
- **Memory file:** `~/.claude/projects/-Users-khalid-Desktop/memory/ag-drone-project.md` (an agent memory note).

---

## 8. Model v2: data, training, results (Vast: `/workspace/agri/`)

**Environment:** `/workspace/agri/.venv` with torch 2.11+cu128, timm 1.0.30, ultralytics 8.4.194, onnx, onnxruntime. Code lives in `/workspace/agri/v2/` as copies of `ml/v2/*`.

**Data** (`raw/` → `data/manifest_cached.csv`, images resized to ≤640 px in `data/cache/`):
- **Sources:**
  - PlantVillage tomato (lab, plus segmented leaves used for background-paste augmentation);
  - **PlantDoc** classification and **object-detection** boxes, cropped into leaf crops; other-plant leaves become negatives;
  - **Tomato-Village** Variant-a (field, 8 classes);
  - **PlantWild v2** (in the wild; tomato classes; other plants become hard negatives);
  - Imagenette 160 (everyday objects);
  - **491 drone-camera frames** from the Pi as negatives.
- **16 classes:** bacterial_spot, early_blight, late_blight, leaf_mold, septoria_leaf_spot, spider_mites, target_spot, yellow_leaf_curl_virus, mosaic_virus, healthy, leaf_miner, spotted_wilt_virus, magnesium_deficiency, nitrogen_deficiency, potassium_deficiency, no_tomato_leaf. **spider_mites and target_spot are lab-only**: no field test data exists for them.
- **Splits:** train 29,129 / val 3,038 / **test_field 1,903** (868 field leaves + 1,035 negatives; held out) / test_lab 902.
  - **Leak control:** perceptual hash, Hamming distance ≤ 6 against any test image. **572 near-duplicates and 16 same-source items dropped.**
  - **Note:** "val field macro-F1" in the logs is a **mislabel**. Validation mixes lab and field images. Only `test_field` is field-only.

**Training** (`ml/v2/train.py`; teacher and student modes):
- **Teacher:** timm, bf16, AdamW + layer decay, mixup/cutmix, label smoothing, EMA, cosine, balanced sampler with field ×3, checkpoint chosen on validation.
- **Student:** consistent distillation, as in Beyer et al. 2022: the same augmented view goes to the teacher(s) at their native resolution and to the student at 224. Options `--kd-weight` and `--init-from`.
- Measured throughput on the GPU: DINOv2-L@448 85 img/s, ConvNeXt-V2-L@384 82, EVA02-L@448 53 (not used). The data loader ran at about 1,300 img/s, so it wasn't the bottleneck.

**Leaf detector:** `train_detector.py`, YOLO11n at 480, class-agnostic "leaf", **PlantDoc whole-leaf boxes only**. Tomato-Village boxes mark lesion patches (median 0.7 % of the image) and made the detector worse (mAP50 0.40 → **0.93** once removed). Result: mAP50 0.931, recall 0.894, precision 0.829. Exported as ONNX opset 12, 480 px.

**Results on held-out field data** (`runs/*/report.json`; `results/baseline.json`; `baseline_val.json`):

| Model | Field acc | Field macro-F1 | False disease on negatives | Notes |
|---|---|---|---|---|
| Baseline (team, MobileNetV3-S, PlantVillage only) | 37.9 % | 0.231 | 99.1 % | val (lab+field) F1 0.615 on its own 6 classes |
| Teacher DINOv2-L @448 (`runs/dinov2L`) | 87.1 % | 0.856 | 3.5 % | val 0.941; weakest: bacterial spot 0.53, septoria 0.68, mosaic 0.71; by source: PlantDoc 75–79 %, PlantWild 92 %, Tomato-Village 97 %, drone camera 98.8 % |
| Student ConvNeXt-Atto (`runs/s_cnxatto`) | 77.3 % | 0.770 | 23.5 % | rejected |
| **Student MobileNetV3-L = v2.0 (DEPLOYED)** (`runs/s_mnv3L`) | **81.6 %** | **0.807** | **15.6 %** | val 0.907; calibration T=0.727, uncertain below 0.70 → 84 % answered at **88.9 %** accuracy; ECE 0.079→0.047; CPU fp32 rescore matches |
| **v2.1 = fine-tune of v2.0** (`runs/s_mnv3L_ft`, kd 0.5, 10 epochs) | **82.9 %** | **0.808** | **12.1 %** | **won on validation** (0.912 vs 0.907; no-leaf 0.876 vs 0.829). Packaged at `package/tomato-v2.1` (name tomato-v2.1, T=0.693). **Not deployed or copied (the user interrupted the deploy)** |
| Teacher ConvNeXt-V2-L @384 (`runs/convnextv2L`) | ? | ? | ? | Was at epoch 8 of 12, val 0.943 (above DINOv2), at last check. **Ensemble decision pending** via `compare_val.py`; re-distil only if it wins on validation |

**On-device (Pi 4, cv2.dnn, 4 threads):**
- Classifiers: MobileNetV3-L@224 38 ms, ConvNeXt-Atto 74 ms, EfficientNet-B0 106 ms.
- Detectors: YOLO11n@416 180 ms, @640 416 ms; YOLO11s@416 471 ms.
- In the live pipeline: about 420 ms detect plus about 40 ms per leaf, so **0.47–0.76 s per frame** with 1–8 leaves while the console runs.
- **Parity:** Pi vs PyTorch top-1 39/40; ONNX vs PyTorch max difference 3.6e-6. Files are byte-identical on the Mac and the Pi: classifier sha256 `5d91c15b8cc61b5f…`, detector `65eb9d12a37fb2d0…`.

**Other ml/v2 scripts:**
- `finalize.py`: temperature scaling and threshold on validation, ONNX export with parity check, `model.json` model card, test metrics without TTA.
- `compare_val.py`: ensemble decision on validation.
- `eval_baseline.py`, `bench.py`, `export_candidates.py`, `pi_bench.py`.
- `parity_ref.py` / `parity_pi.py`, `pi_pipeline_check.py`, `sample_test_images.py`, `make_print_set.py`.
- `live_eval.py`: scores test-mode captures from `detections.jsonl`.
- `train_env.py`: learned sensor model. It refuses to train below 120 labelled records or 20 per class.

---

## 9. Environmental sensors (BME680 + soil moisture), `raspberry_pi/env_risk.py`

- **No sensor hardware exists yet.** Nodes or a LoRa gateway POST JSON to `/api/sensors`, single or batched, up to 500 readings, with range validation. Accepted fields: `temp_c`, `rh`, `pressure_hpa`, `gas_ohm`, `soil_moisture_pct`, `node`, `t`.
- **Rules** (leaf wetness approximated as hours at RH ≥ 90 %):
  - **late blight:** Hutton criteria;
  - **early blight / septoria:** TOM-CAST DSV over 7 days (threshold ~15);
  - **leaf mould:** RH > 85 % at 18–27 °C;
  - **bacterial spot:** warm (24–30 °C) and wet;
  - **spider mites:** hot and dry;
  - **soil note** for dry or waterlogged conditions;
  - **pressure** is context only; **gas resistance** is logged only, because it has no validated link.
- **Fusion is boost-only and bounded** (×1.0 to ×1.35). It never lowers a disease the camera sees.
- **Honest limit:** nothing has been supervised-trained on sensor data, because no paired data exists. `train_env.py` is ready for the user's real field data.

---

## 10. Open items / next steps (priority order)

1. **Recover v2.1.** Restart the Vast instance (if it was only stopped), then run `scp -P <port> "root@<host>:/workspace/agri/package/tomato-v2.1/*" ml/models/tomato-v2.1/`. If the instance was destroyed, v2.1 can be reproduced: re-run `ml/v2` from `download.sh` → `prepare.py` → `cache.py` → `train.py` (DINOv2 teacher, then student, then the fine-tune with `--init-from --kd-weight 0.5 --lr 4e-4 --epochs 10`) → `finalize.py`. That's about 3 GPU-hours. Copy any `runs/*/best.pt` worth keeping too (DINOv2 is about 1.2 GB). Then deploy v2.1:
   - back up `ml/models/tomato-v2` → `tomato-v2.0` on the Pi;
   - rsync v2.1 into `tomato-v2/`, then `sync`;
   - restart `crop-console`.
2. **ConvNeXt-V2-L:** check `runs/convnextv2L/report.json`. Run `python compare_val.py ../runs/dinov2L ../runs/convnextv2L`. If the ensemble wins on validation, re-distil the MobileNetV3-L student with `--teachers <both best.pt>`, then finalize and compare with v2.1 on validation.
3. **Vast billing:** the instance appears stopped as of 2026-11-06. Destroy it only after the artifacts are saved, and ask the user first.
4. **Live demo / test** with real tomato leaves and `demo/print_set.pdf` in test mode, then `live_eval.py`.
5. **Weak spots to improve:**
   - false disease on **other plants' leaves** (12–15 %; drone lab scenes are 98.8 % correct);
   - the bacterial spot, septoria and mosaic classes;
   - more field data, e.g. a teammate's Roboflow tomato set as an additional held-out source.
6. **Hardware:** a 5 V 5 A UBEC for the Pi; a downward camera mount with damping; a telemetry radio (SiK, legal band); the RTL switch on CH7; geofence and RTL altitude; accelerometer calibration.
7. **Boot persistence** of the console and tunnel: the user runs the runbook §5 command. It was classifier-blocked for agents.
8. **Security:** change the Pi password; use a longer console password; rotate the Supabase and R2 secrets in `~/.claude/settings.json`.
9. **Autonomy path:** an optical-flow sensor (or motion capture), then Loiter indoors, then a Guided take-off / hold / land script (test in SITL first, in the `drone-sim` image), then outdoor GPS survey missions (needs GCAA permission).
10. **Flight logs:** download and analyse #57 (the lift-off incident) and #59/#60.
11. Ask before pushing the new code to Jamila's repo (for example as a PR).
12. **Deploy the Farm Command Center to the Pi** (`docs/COMMAND_CENTER.md` → Deploying). Verify read-only indoors: pre-flight must show blocked (no GPS). Before any real autonomous flight, do the missing-hardware list in that doc: UBEC, nadir camera, measured FOV and endurance, geofence, RTL switch, GCAA permission.

---

## 11. Gotchas (hard-won)

- **`pkill -f` / `pgrep -f` with a pattern that appears in the same ssh command kills your own shell.** Kill by PID, by port (`ss -ltnp`), or in a separate command. Bracket patterns don't help if the text appears elsewhere in the command.
- **Inline `python -c` / heredocs inside `ssh '…'` break on quotes.** Write scripts to files and scp them.
- **`ssh … nohup … &` can hang the session.** Use `setsid nohup … < /dev/null & disown` or `systemd-run`.
- **The Pi can lose power abruptly.** Always `sync` after deploys; freshly written files have been zeroed before.
- **The camera `/dev/mediaN` number changes between boots.** UYVY format must match on both the sensor and the capture node.
- **Flask serves templates and static files from disk.** Updating the live folder's JS or HTML changes the running console immediately, even with an old backend. Test in `~/console-dev`.
- **The Pixhawk reports "armed" during a motor test**, so wait about 4 s between `motor_test.py` runs.
- **The Mac often auto-joins AL FAHIM vs the hotspot**, so check `ipconfig getifaddr en0` when SSH fails.
- **The Vast `llama` service takes 32 GB of VRAM.** Leave it alone.
- **The `pip install` on the Mac was rejected once by the user.** Don't install things on the Mac without asking.
- **QGroundControl:** a Pixhawk on a power-only USB cable doesn't enumerate. Use a data cable. When flashing, pick **ArduPilot → ChibiOS → Multi-Rotor → Pixhawk1 → stable**; it defaults to PX4, so avoid that.
