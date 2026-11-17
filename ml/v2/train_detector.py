#!/usr/bin/env python3
"""Train the class-agnostic leaf detector (YOLO11) and export it to ONNX for the Pi."""
import argparse
import json

from ultralytics import YOLO

ap = argparse.ArgumentParser()
ap.add_argument("--model", default="yolo11s.pt")
ap.add_argument("--imgsz", type=int, default=640)
ap.add_argument("--epochs", type=int, default=100)
ap.add_argument("--batch", type=int, default=32)
args = ap.parse_args()

model = YOLO(args.model)
model.train(data="/workspace/agri/det/leaf.yaml", imgsz=args.imgsz, epochs=args.epochs, batch=args.batch,
            workers=24, device=0, cos_lr=True, close_mosaic=10, patience=30, project="/workspace/agri/runs",
            name="leafdet", exist_ok=True, plots=True, verbose=False)
metrics = model.val(data="/workspace/agri/det/leaf.yaml", imgsz=args.imgsz, split="val")
report = {"mAP50": float(metrics.box.map50), "mAP50_95": float(metrics.box.map),
          "precision": float(metrics.box.mp), "recall": float(metrics.box.mr)}
json.dump(report, open("/workspace/agri/runs/leafdet/report.json", "w"), indent=2)
print("DETECTOR:", report)
# opset 12 + static shapes: the most compatible graph for OpenCV's cv2.dnn on the Pi.
best = YOLO("/workspace/agri/runs/leafdet/weights/best.pt")
best.export(format="onnx", imgsz=args.imgsz, opset=12, simplify=True, dynamic=False)
print("DONE")
