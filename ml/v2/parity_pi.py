#!/usr/bin/env python3
"""On the Pi: compare the deployed cv2.dnn classifier with the PyTorch reference predictions."""
import json
import sys

import cv2
import numpy as np

sys.path.insert(0, sys.argv[3])
from pipeline import LeafPipeline  # noqa: E402

pipe = LeafPipeline(sys.argv[1])
ref = json.load(open(f"{sys.argv[2]}/ref.json"))
agree, diffs, correct = 0, [], 0
for name, r in sorted(ref.items()):
    p = pipe.classify([cv2.imread(f"{sys.argv[2]}/{name}")])[0]
    diffs.append(float(np.abs(p - np.array(r["probs"])).max()))
    pred = pipe.classes[int(p.argmax())]
    agree += pred == r["pred"]
    correct += pred == r["truth"]
print(f"top-1 agreement with PyTorch: {agree}/{len(ref)} | max prob diff median {np.median(diffs):.4f}, "
      f"max {max(diffs):.4f} | accuracy on these {correct}/{len(ref)}")
