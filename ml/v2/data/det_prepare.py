#!/usr/bin/env python3
"""Build a class-agnostic YOLO 'leaf' detection dataset.

Source: PlantDoc object-detection boxes (whole field leaves of many plants). Tomato-Village
Variant-c is NOT used: its boxes mark symptom patches (median 0.7 % of the image), not whole
leaves, and mixing the two definitions of a box degraded the detector. Drone-camera and
everyday images are added with empty label files so the detector learns not to fire on lab scenes.
"""
import csv
import hashlib
import random
import shutil
from collections import defaultdict
from pathlib import Path

RAW = Path("/workspace/agri/raw")
OUT = Path("/workspace/agri/det")


def split_of(name, val=0.1):
    return "val" if int(hashlib.md5(name.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF < val else "train"


def put(src, split, lines, stem):
    (OUT / "images" / split / f"{stem}{Path(src).suffix.lower()}").write_bytes(Path(src).read_bytes())
    (OUT / "labels" / split / f"{stem}.txt").write_text("\n".join(lines) + ("\n" if lines else ""))


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    for s in ("train", "val"):
        (OUT / "images" / s).mkdir(parents=True)
        (OUT / "labels" / s).mkdir(parents=True)
    n = defaultdict(int)

    # PlantDoc boxes (all plants -> 'leaf'); its TEST split becomes our detector val split.
    for csv_name, img_dir, split in [("train_labels.csv", "TRAIN", "train"), ("test_labels.csv", "TEST", "val")]:
        boxes = defaultdict(list)
        for r in csv.DictReader(open(RAW / "plantdoc_od" / csv_name)):
            boxes[r["filename"]].append(r)
        for fname, rs in boxes.items():
            src = RAW / "plantdoc_od" / img_dir / fname
            if not src.exists():
                continue
            lines = []
            for r in rs:
                w, h = float(r["width"]), float(r["height"])
                x0, y0, x1, y1 = (float(r[k]) for k in ("xmin", "ymin", "xmax", "ymax"))
                if w <= 0 or h <= 0 or x1 <= x0 or y1 <= y0:
                    continue
                lines.append(f"0 {(x0 + x1) / 2 / w:.6f} {(y0 + y1) / 2 / h:.6f} {(x1 - x0) / w:.6f} {(y1 - y0) / h:.6f}")
            if lines:
                put(src, split, lines, "pd_" + Path(fname).stem)
                n[f"plantdoc_{split}"] += 1

    # Background images (no leaves) from the drone camera and everyday scenes.
    bgs = sorted((RAW / "drone_negatives").glob("*.jpg"))
    imn = sorted((RAW / "imagenette" / "train").rglob("*.JPEG"))
    random.Random(0).shuffle(imn)
    for p in bgs + imn[:600]:
        s = split_of(p.stem, 0.15)
        put(p, s, [], "bg_" + p.stem)
        n[f"background_{s}"] += 1

    (OUT / "leaf.yaml").write_text(f"path: {OUT}\ntrain: images/train\nval: images/val\nnames:\n  0: leaf\n")
    print(dict(n))


if __name__ == "__main__":
    main()
