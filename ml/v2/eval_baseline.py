#!/usr/bin/env python3
"""Score the team's original ml/model.onnx (6 tomato classes, no 'no leaf' class) on the
held-out field test, so v2 can be compared against an honest 'before' number."""
import json
import sys

import numpy as np
import onnxruntime as ort
from PIL import Image
from sklearn.metrics import f1_score

from common import CLASSES, NO_LEAF, NOT_DISEASE, read_manifest

BASE_TO_V2 = ["bacterial_spot", "early_blight", "late_blight", "leaf_mold", "septoria_leaf_spot", "healthy"]
MEAN, STD = np.array([0.485, 0.456, 0.406], np.float32), np.array([0.229, 0.224, 0.225], np.float32)

model, manifest, out = sys.argv[1], sys.argv[2], sys.argv[3]
sess = ort.InferenceSession(model, providers=["CPUExecutionProvider"])
rows = read_manifest(manifest, {"test_field"})
known = {CLASSES.index(c) for c in BASE_TO_V2}

y_true, y_pred = [], []
for r in rows:
    img = Image.open(r["path"]).convert("RGB").resize((224, 224), Image.Resampling.BILINEAR)
    x = ((np.asarray(img, np.float32) / 255 - MEAN) / STD).transpose(2, 0, 1)[None]
    logits = sess.run(None, {sess.get_inputs()[0].name: x})[0][0]
    y_true.append(r["y"])
    y_pred.append(CLASSES.index(BASE_TO_V2[int(logits.argmax())]))
y_true, y_pred = np.array(y_true), np.array(y_pred)

leaf = np.isin(y_true, list(known))       # only classes the baseline knows
neg = y_true == NO_LEAF
res = {
    "model": "baseline MobileNetV3-Small (PlantVillage only)",
    "scored_leaf_images": int(leaf.sum()),
    "field_accuracy_on_its_6_classes": float((y_true[leaf] == y_pred[leaf]).mean()),
    "field_macro_f1_on_its_6_classes": float(f1_score(y_true[leaf], y_pred[leaf], labels=sorted(known),
                                                      average="macro", zero_division=0)),
    "negative_images": int(neg.sum()),
    "false_disease_rate_on_negatives": float(np.mean([p not in NOT_DISEASE for p in y_pred[neg]])),
    "note": "Baseline has no 'no tomato leaf' class, so it cannot reject non-leaf images.",
}
json.dump(res, open(out, "w"), indent=2)
print(json.dumps(res, indent=2))
