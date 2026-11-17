#!/usr/bin/env python3
"""Copy one held-out field image per (source, class) into a folder, for quick on-device checks."""
import csv
import os
import shutil
import sys

out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/leafsamples"
limit = int(sys.argv[2]) if len(sys.argv) > 2 else 6
os.makedirs(out, exist_ok=True)
seen = set()
for r in csv.DictReader(open("/workspace/agri/data/manifest_cached.csv")):
    key = (r["source"], r["label"])
    if r["split"] != "test_field" or r["label"] == "no_tomato_leaf" or key in seen:
        continue
    if r["source"] not in ("plantdoc", "plantwild", "tomato_village"):
        continue
    seen.add(key)
    shutil.copy(r["path"], os.path.join(out, f"{r['source']}__{r['label']}.jpg"))
    if len(seen) >= limit:
        break
print(sorted(os.listdir(out)))
