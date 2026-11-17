#!/usr/bin/env python3
"""Make PyTorch reference predictions for a few held-out images (for the Pi parity check)."""
import json
import random
import shutil
import sys
from pathlib import Path

import torch

from common import CLASSES, ManifestDataset, eval_transform, read_manifest
from train import build

ck = torch.load(sys.argv[1], map_location="cpu", weights_only=False)
out = Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
model = build(ck["model_name"], ck["res"], pretrained=False)
model.load_state_dict(ck["state_dict"])
model.eval()
T = json.load(open(sys.argv[3]))["classifier"]["temperature"]
rows = read_manifest("/workspace/agri/data/manifest_cached.csv", {"test_field"})
rows = random.Random(7).sample(rows, 40)
ds = ManifestDataset(rows, eval_transform(ck["res"]))
ref = {}
with torch.no_grad():
    for i, r in enumerate(rows):
        name = f"p{i:02d}.jpg"
        shutil.copy(r["path"], out / name)
        p = torch.softmax(model(ds[i][0][None]) / T, 1)[0]
        ref[name] = {"probs": p.tolist(), "pred": CLASSES[int(p.argmax())], "truth": r["label"]}
json.dump(ref, open(out / "ref.json", "w"))
print(f"wrote {len(ref)} reference predictions")
