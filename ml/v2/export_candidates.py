#!/usr/bin/env python3
"""Export untrained student/detector candidates to ONNX, only to time them on the Pi
(inference speed does not depend on the weights)."""
from pathlib import Path

import timm
import torch
from ultralytics import YOLO

OUT = Path("/workspace/agri/pi_bench")
OUT.mkdir(exist_ok=True)
for name, res in [("mobilenetv3_large_100", 224), ("mobilenetv3_large_100", 256), ("efficientnet_b0", 224),
                  ("efficientnet_b0", 256), ("efficientnet_b2", 288), ("convnext_atto", 224)]:
    m = timm.create_model(name, num_classes=16, pretrained=False).eval()
    torch.onnx.export(m, torch.randn(1, 3, res, res), OUT / f"cls_{name}_{res}.onnx", opset_version=17,
                      input_names=["image"], output_names=["logits"], dynamo=False)
for w in ("yolo11n.pt", "yolo11s.pt"):
    for sz in (320, 416, 640):
        path = YOLO(w).export(format="onnx", imgsz=sz, opset=12, simplify=True, dynamic=False)
        Path(path).rename(OUT / f"det_{Path(w).stem}_{sz}.onnx")
print(sorted(p.name for p in OUT.glob("*.onnx")))
