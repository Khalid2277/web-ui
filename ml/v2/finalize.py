#!/usr/bin/env python3
"""Calibrate, export and package the student for the Pi.

python finalize.py --student runs/mnv3L/best.pt --detector runs/leafdet --out /workspace/agri/package/tomato-v2
Produces classifier.onnx, detector.onnx, model.json (classes, preprocessing, temperature,
uncertainty threshold, held-out metrics). Metrics are computed WITHOUT test-time
augmentation, exactly as the Pi runs the model.
"""
import argparse
import json
import shutil
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort
import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader

from common import CLASSES, MEAN, NO_LEAF, STD, ManifestDataset, eval_transform, metrics, read_manifest
from train import build, predict

ap = argparse.ArgumentParser()
ap.add_argument("--student", required=True)
ap.add_argument("--detector", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--manifest", default="/workspace/agri/data/manifest_cached.csv")
ap.add_argument("--teacher-report", default="")
args = ap.parse_args()
out = Path(args.out)
out.mkdir(parents=True, exist_ok=True)

ck = torch.load(args.student, map_location="cpu", weights_only=False)
model = build(ck["model_name"], ck["res"], pretrained=False)
model.load_state_dict(ck["state_dict"])
model = model.cuda().eval()
res = ck["res"]

val_rows = read_manifest(args.manifest, {"val"})
test_rows = read_manifest(args.manifest, {"test_field"})
lab_rows = read_manifest(args.manifest, {"test_lab"})
loader = lambda rows: DataLoader(ManifestDataset(rows, eval_transform(res)), batch_size=256, num_workers=16)
val_logits = predict(model, loader(val_rows), "cuda", tta=False)
test_logits = predict(model, loader(test_rows), "cuda", tta=False)
lab_logits = predict(model, loader(lab_rows), "cuda", tta=False)
val_y = torch.tensor([r["y"] for r in val_rows])

# ---- temperature scaling (fit on val only)
log_t = torch.zeros(1, requires_grad=True)
opt = torch.optim.LBFGS([log_t], lr=0.1, max_iter=200)
def closure():
    opt.zero_grad()
    loss = F.cross_entropy(val_logits / log_t.exp(), val_y)
    loss.backward()
    return loss
opt.step(closure)
T = float(log_t.exp())

def ece(logits, y, temp, bins=15):
    p = F.softmax(logits / temp, 1)
    conf, pred = p.max(1)
    acc = (pred == y).float()
    e = 0.0
    for lo in np.linspace(0, 1, bins + 1)[:-1]:
        m = (conf > lo) & (conf <= lo + 1 / bins)
        if m.any():
            e += m.float().mean() * (conf[m].mean() - acc[m].mean()).abs()
    return float(e)

# ---- uncertainty threshold (chosen on val): highest threshold keeping >= 90% of val leaves answered
vp = F.softmax(val_logits / T, 1)
conf, pred = vp.max(1)
leaf = val_y != NO_LEAF
table = []
for th in [0.3, 0.4, 0.5, 0.6, 0.7, 0.8]:
    answered = leaf & (conf >= th)
    table.append({"threshold": th, "coverage": float(answered.sum() / leaf.sum()),
                  "accuracy_when_answered": float((pred[answered] == val_y[answered]).float().mean())})
chosen = max([t for t in table if t["coverage"] >= 0.90], key=lambda t: t["threshold"], default=table[0])

# ---- held-out metrics, as deployed (no TTA)
test_y = [r["y"] for r in test_rows]
tm = metrics(test_y, (test_logits / T).argmax(1).tolist(), test_rows)
tp = F.softmax(test_logits / T, 1)
tconf, tpred = tp.max(1)
ty = torch.tensor(test_y)
tleaf = ty != NO_LEAF
answered = tleaf & (tconf >= chosen["threshold"])
tm["with_uncertain_state"] = {"threshold": chosen["threshold"],
                              "coverage": float(answered.sum() / tleaf.sum()),
                              "accuracy_when_answered": float((tpred[answered] == ty[answered]).float().mean())}
tm["ece_before"], tm["ece_after"] = ece(test_logits, ty, 1.0), ece(test_logits, ty, T)
lab_acc = float((lab_logits.argmax(1) == torch.tensor([r["y"] for r in lab_rows])).float().mean())

# ---- export classifier to ONNX + parity check
onnx_path = out / "classifier.onnx"
cpu = model.float().cpu().eval()
dummy = torch.randn(1, 3, res, res)
torch.onnx.export(cpu, dummy, onnx_path, opset_version=17, input_names=["image"], output_names=["logits"], dynamo=False)
sess = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
x = torch.stack([ManifestDataset(val_rows[:8], eval_transform(res))[i][0] for i in range(8)])
diff = max(float(np.abs(sess.run(None, {"image": x[i:i + 1].numpy()})[0] - cpu(x[i:i + 1]).detach().numpy()).max())
           for i in range(8))

# ---- detector
det_dir = Path(args.detector)
shutil.copy(next((det_dir / "weights").glob("best.onnx")), out / "detector.onnx")
det_report = json.load(open(det_dir / "report.json"))
det_args = (det_dir / "args.yaml").read_text()
det_imgsz = int(next(l for l in det_args.splitlines() if l.startswith("imgsz:")).split(":")[1])

teacher = json.load(open(args.teacher_report)) if args.teacher_report else None
meta = {
    "name": "tomato-v2", "version": time.strftime("%Y.%m.%d"), "classes": CLASSES,
    "classifier": {"architecture": ck["model_name"], "input_size": res, "mean": list(MEAN), "std": list(STD),
                   "temperature": round(T, 4), "uncertain_below": chosen["threshold"],
                   "onnx_max_abs_diff_vs_pytorch": diff},
    "detector": {"architecture": "YOLO11n (class-agnostic leaf)", "input_size": det_imgsz, "conf": 0.35,
                 "iou": 0.5, "max_leaves": 8, "val": det_report},
    "metrics": {"held_out_field_test": tm, "lab_test_accuracy": lab_acc, "val_threshold_table": table},
    "teacher": {k: teacher[k] for k in ("model", "res", "test_field")} if teacher else None,
    "training_data": "PlantVillage (lab) + PlantDoc, Tomato-Village, PlantWild (field) + drone-camera and everyday-object negatives",
    "lab_only_classes": ["spider_mites", "target_spot"],
}
json.dump(meta, open(out / "model.json", "w"), indent=2)
print(json.dumps({"temperature": T, "threshold": chosen, "onnx_diff": diff, "lab_acc": lab_acc,
                  "test_field": {k: tm[k] for k in ("field_accuracy", "field_macro_f1", "no_leaf_recall",
                                                   "false_disease_rate_on_negatives", "with_uncertain_state",
                                                   "ece_before", "ece_after", "accuracy_by_source")}}, indent=2))
