#!/usr/bin/env python3
"""Decide on VALIDATION whether an ensemble of teachers beats the best single teacher.
(The held-out field test is not used for this decision.)

python compare_val.py runs/dinov2L runs/convnextv2L
"""
import itertools
import sys

import numpy as np
import torch

from common import metrics, read_manifest

rows = read_manifest("/workspace/agri/data/manifest_cached.csv", {"val"})
y = [r["y"] for r in rows]
probs = {d.rstrip("/").split("/")[-1]: torch.softmax(torch.tensor(np.load(f"{d}/val_logits.npy")), 1) for d in sys.argv[1:]}
results = {}
for k in range(1, len(probs) + 1):
    for combo in itertools.combinations(probs, k):
        p = torch.stack([probs[c] for c in combo]).mean(0)
        m = metrics(y, p.argmax(1).tolist(), rows)
        results[" + ".join(combo)] = (m["field_macro_f1"], m["no_leaf_recall"])
for name, (f1, nl) in sorted(results.items(), key=lambda kv: -kv[1][0]):
    print(f"{name:40s} val field macro-F1 {f1:.4f}   no-leaf recall {nl:.4f}")
