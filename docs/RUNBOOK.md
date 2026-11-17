# Crop Health Drone: Runbook

Run the drone's AI console without help. Commands marked **Mac** run in the Mac's Terminal.
Commands marked **Pi** run after you have connected to the Pi (step 1).

## 1. Connect

The Mac and the Pi must be on the same Wi-Fi. At home that's AL FAHIM; elsewhere it's the iPhone hotspot "K’s iPhone". Keep the hotspot settings screen open while the Pi boots.

**Mac:**
```bash
ssh drones@raspberrypi.local
```
If that name doesn't resolve, use the IP shown in the console header, e.g. `ssh drones@172.20.10.2` on the hotspot.

## 2. Start the console (Pi)

Starts the AI console in the background. It keeps running after you disconnect.
```bash
sudo systemd-run --unit=crop-console --uid=drones --gid=drones --working-directory=/home/drones/disease-detection --setenv=HOME=/home/drones --property=Restart=on-failure /usr/bin/python3 /home/drones/disease-detection/raspberry_pi/dashboard/app.py
```
To open it from the same Wi-Fi, go to **http://raspberrypi.local:8000**. Any username works, with the console password.
That opens the **Farm Command Center** (overview, operations, map, disease log, analytics, missions, system). The original console is at **/console**.
Drone commands (upload, start, pause, return, land) only work from the drone's local Wi-Fi; the public link is view-only. Full guide: `docs/COMMAND_CENTER.md`.

## 3. Start the public link (Pi, optional)

This makes the console reachable from any network. The link changes every time the tunnel starts.
```bash
sudo systemd-run --unit=crop-tunnel --uid=drones --gid=drones --setenv=HOME=/home/drones --property=Restart=on-failure /usr/bin/cloudflared tunnel --no-autoupdate --url http://localhost:8000
```
Print the current public link (wait about 10 s after starting the tunnel):
```bash
sh ~/disease-detection/raspberry_pi/systemd/crop-link
```

## 4. Status, logs, stop, restart (Pi)

```bash
systemctl is-active crop-console crop-tunnel
```
```bash
journalctl -u crop-console -f
```
```bash
sudo systemctl stop crop-console crop-tunnel
```
After a stop, the start commands in steps 2 and 3 need this first:
```bash
sudo systemctl reset-failed crop-console crop-tunnel
```

## 5. Start automatically at every boot (optional, one-time)

This publishes the console via the tunnel whenever the Pi is on. The tunnel refuses to start without a password.
```bash
S=~/disease-detection/raspberry_pi/systemd; sudo install -m 644 $S/crop-console.service $S/crop-tunnel.service /etc/systemd/system/ && sudo install -m 755 $S/crop-link /usr/local/bin/ && sudo systemctl daemon-reload && sudo systemctl enable --now crop-console crop-tunnel
```
To undo:
```bash
sudo systemctl disable --now crop-console crop-tunnel
```

## 6. Change the console password (Pi)

```bash
read -rsp "New console password: " p && echo && printf "%s" "$p" > ~/.config/crop-console/password && chmod 600 ~/.config/crop-console/password && echo saved
```
It takes effect immediately; no restart needed.

## 7. Live test and demo

1. Print `demo/print_set.pdf` (30 held-out photos). Keep `demo/answer_key.csv` away from the camera.
2. In the console, under **Test mode**, pick the true label (and type e.g. "printed T07 at 60 cm"). Then press **Capture & log detection**.
3. Score the session:

```bash
python3 ~/disease-detection/ml/v2/live_eval.py
```

Hold items 30–60 cm from the camera in good light. The camera currently faces sideways.

## 8. Field sensors (BME680 + soil moisture)

Nodes or a LoRa gateway POST readings to the console (password protected). Example from a node:
```bash
curl -u node:YOUR_CONSOLE_PASSWORD -H "Content-Type: application/json" -d '{"node":"bed-1","temp_c":24.1,"rh":88,"pressure_hpa":1009,"gas_ohm":52000,"soil_moisture_pct":41}' http://raspberrypi.local:8000/api/sensors
```
Once at least 120 real readings with confirmed diagnoses exist, train the learned sensor model:
```bash
python3 ~/disease-detection/ml/v2/train_env.py --sensors ~/disease-detection/raspberry_pi/dashboard/captures/sensors.jsonl --labels ~/disease-detection/raspberry_pi/dashboard/captures/detections.jsonl
```

## 9. Things that need the console stopped first

The console holds the camera and the Pixhawk serial port. Stop it (step 4) before running any of these.

One-shot diagnosis of a camera frame:
```bash
cd ~/disease-detection && python3 raspberry_pi/infer.py --camera --save shot.jpg
```
Spin one motor (props OFF) at 7 % for 2 s. Motors are 1 to 4:
```bash
python3 /home/drones/motor_test.py 1
```

## 10. Shut down safely (Pi)

Never just pull the power: the SD card can corrupt. Wait for the green light to stop flickering.
```bash
sudo shutdown -h now
```

## Models

- **Deployed:** `ml/models/tomato-v2` = v2.0 (MobileNetV3-L student + YOLO11n leaf detector).
  - Held-out field accuracy 81.6 %, macro-F1 0.807, false disease 15.6 %.
  - Baseline was 37.9 % / 0.23 / 99 %.
- **v2.1** (better on validation; held-out accuracy 82.9 %, false disease 12.1 %) is packaged on the Vast GPU at `/workspace/agri/package/tomato-v2.1` but **not yet deployed**.
- The Vast instance storage is **not persistent**. Copy anything you want to keep before stopping or destroying it.
