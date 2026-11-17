#!/usr/bin/env python3
"""Run the deployed LeafPipeline on a folder of images on the Pi and save annotated copies."""
import glob
import sys

import cv2

sys.path.insert(0, sys.argv[3] if len(sys.argv) > 3 else "/home/drones/disease-detection/raspberry_pi")
from pipeline import LeafPipeline  # noqa: E402

pkg, folder = sys.argv[1], sys.argv[2]
p = LeafPipeline(pkg)
for f in sorted(glob.glob(f"{folder}/*.jpg")):
    if f.endswith("_det.jpg"):
        continue
    img = cv2.imread(f)
    r = p.run(img)
    h, w = img.shape[:2]
    for i, l in enumerate(r["leaves"]):
        x0, y0, x1, y1 = int(l["box"][0] * w), int(l["box"][1] * h), int(l["box"][2] * w), int(l["box"][3] * h)
        cv2.rectangle(img, (x0, y0), (x1, y1), (0, 0, 255), max(2, w // 300))
        cv2.putText(img, f"{i + 1} {l['label']} {l['p']:.0%}", (x0, max(20, y0 - 8)), cv2.FONT_HERSHEY_SIMPLEX,
                    max(0.6, w / 1200), (0, 0, 255), max(1, w // 600))
    cv2.imwrite(f[:-4] + "_det.jpg", img)
    head = r["leaves"] and [(l["label"], round(l["p"], 2), l["status"]) for l in r["leaves"]] or [("frame", r["frame"]["label"], round(r["frame"]["p"], 2))]
    print(f"{f.split('/')[-1][:48]:48s} leaves {len(r['leaves'])} | {r['ms_total']} ms | {head[:4]}")
