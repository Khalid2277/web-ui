#!/usr/bin/env python3
"""Run the disease-classification model (ml/model.onnx) on the Raspberry Pi.

Uses the Pi's existing OpenCV (cv2.dnn), so no extra packages are needed.
Preprocessing follows ml/README: RGB, 224x224, scaled to 0-1, ImageNet mean/std.

Usage:
  python3 raspberry_pi/infer.py --image path/to/leaf.jpg [more.jpg ...]
  python3 raspberry_pi/infer.py --camera            # one frame from /dev/video0
  python3 raspberry_pi/infer.py --camera --save frame.jpg
"""
import argparse
import json
import re
import subprocess
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def preprocess(bgr):
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    rgb = cv2.resize(rgb, (224, 224), interpolation=cv2.INTER_AREA)
    x = (rgb.astype(np.float32) / 255.0 - MEAN) / STD
    return x.transpose(2, 0, 1)[np.newaxis]  # 1x3x224x224


def classify(net, classes, bgr, top=3):
    net.setInput(preprocess(bgr))
    logits = net.forward()[0]
    probs = np.exp(logits - logits.max())
    probs /= probs.sum()
    order = np.argsort(probs)[::-1][:top]
    return [(classes[i], float(probs[i])) for i in order]


def grab_camera_frame(device, width=3840, height=2160):
    """Capture one frame from the e-con AR1335 camera.

    The sensor (on /dev/mediaN, whose number changes between boots) and the
    capture node must both be set to UYVY at the same size, otherwise frames
    come back black.
    """
    devices = subprocess.run(["v4l2-ctl", "--list-devices"], capture_output=True, text=True).stdout
    match = re.search(r"unicam.*?(/dev/media\d+)", devices, re.S)
    if not match:
        raise SystemExit("camera not detected (no unicam media device)")
    subprocess.run(["media-ctl", "-d", match.group(1), "--set-v4l2",
                    f'"ar1335_mcu 10-0042":0 [fmt:UYVY8_2X8/{width}x{height}]'], check=True)
    subprocess.run(["v4l2-ctl", "-d", device,
                    f"--set-fmt-video=width={width},height={height},pixelformat=UYVY"], check=True)
    frames = 6  # the first frames are dark while auto-exposure settles
    with tempfile.NamedTemporaryFile(suffix=".uyvy") as raw:
        subprocess.run(["v4l2-ctl", "-d", device, "--stream-mmap", f"--stream-count={frames}",
                        f"--stream-to={raw.name}"], check=True, capture_output=True, timeout=30)
        data = np.fromfile(raw.name, dtype=np.uint8)
    size = width * height * 2
    if len(data) < size:
        raise SystemExit("camera returned no frame")
    last = data[(len(data) // size - 1) * size:][:size].reshape(height, width, 2)
    return cv2.cvtColor(last, cv2.COLOR_YUV2BGR_UYVY)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--image", nargs="+", help="image file(s) to classify")
    p.add_argument("--camera", action="store_true", help="classify one camera frame")
    p.add_argument("--device", default="/dev/video0")
    p.add_argument("--save", help="save the camera frame to this file")
    p.add_argument("--model", default=str(ROOT / "ml" / "model.onnx"))
    p.add_argument("--classes", default=str(ROOT / "ml" / "classes.json"))
    args = p.parse_args()
    if not args.image and not args.camera:
        p.error("give --image FILE or --camera")

    net = cv2.dnn.readNetFromONNX(args.model)
    classes = json.load(open(args.classes))

    inputs = []
    if args.camera:
        frame = grab_camera_frame(args.device)
        if args.save:
            cv2.imwrite(args.save, frame)
        inputs.append(("camera", frame))
    for path in args.image or []:
        img = cv2.imread(path)
        if img is None:
            print(f"{path}: could not read image")
            continue
        inputs.append((path, img))

    for name, img in inputs:
        t = time.time()
        results = classify(net, classes, img)
        ms = (time.time() - t) * 1000
        best, conf = results[0]
        print(f"{name}: {best}  ({conf:.0%})  [{ms:.0f} ms]")
        for label, prob in results[1:]:
            print(f"    {label}: {prob:.0%}")


if __name__ == "__main__":
    main()
