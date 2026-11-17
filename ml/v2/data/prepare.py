#!/usr/bin/env python3
"""Build the ml/v2 classification manifest from the raw datasets.

Output (under --out, default /workspace/agri/data):
  crops/            leaf crops cut from PlantDoc object-detection boxes
  manifest.csv      path,label,source,domain,split,group
  summary.json      per split / class / source counts

Splits:
  train, val   -> used for training / model selection
  test_field   -> HELD OUT field + negative images, never trained on (headline metric)
  test_lab     -> held-out PlantVillage lab images (sanity check only)

Leak control: every field/negative image gets a perceptual hash; any non-test image
within Hamming distance HASH_DIST of a test image is dropped.
"""
import argparse
import csv
import hashlib
import json
import os
import random
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import imagehash
from PIL import Image

HASH_DIST = 6
CLASSES = [
    "bacterial_spot", "early_blight", "late_blight", "leaf_mold", "septoria_leaf_spot",
    "spider_mites", "target_spot", "yellow_leaf_curl_virus", "mosaic_virus", "healthy",
    "leaf_miner", "spotted_wilt_virus", "magnesium_deficiency", "nitrogen_deficiency",
    "potassium_deficiency", "no_tomato_leaf",
]
PV = {
    "Tomato___Bacterial_spot": "bacterial_spot", "Tomato___Early_blight": "early_blight",
    "Tomato___Late_blight": "late_blight", "Tomato___Leaf_Mold": "leaf_mold",
    "Tomato___Septoria_leaf_spot": "septoria_leaf_spot",
    "Tomato___Spider_mites Two-spotted_spider_mite": "spider_mites",
    "Tomato___Target_Spot": "target_spot", "Tomato___Tomato_Yellow_Leaf_Curl_Virus": "yellow_leaf_curl_virus",
    "Tomato___Tomato_mosaic_virus": "mosaic_virus", "Tomato___healthy": "healthy",
}
PD = {
    "Tomato Early blight leaf": "early_blight", "Tomato Septoria leaf spot": "septoria_leaf_spot",
    "Tomato leaf bacterial spot": "bacterial_spot", "Tomato leaf late blight": "late_blight",
    "Tomato leaf mosaic virus": "mosaic_virus", "Tomato leaf yellow virus": "yellow_leaf_curl_virus",
    "Tomato leaf": "healthy", "Tomato mold leaf": "leaf_mold",
    "Tomato two spotted spider mites leaf": "spider_mites",
}
TV = {
    "Early_blight": "early_blight", "Healthy": "healthy", "Late_blight": "late_blight",
    "Leaf Miner": "leaf_miner", "Magnesium Deficiency": "magnesium_deficiency",
    "Nitrogen Deficiency": "nitrogen_deficiency", "Pottassium Deficiency": "potassium_deficiency",
    "Spotted Wilt Virus": "spotted_wilt_virus",
}
PW = {
    "tomato yellow leaf curl virus": "yellow_leaf_curl_virus", "tomato late blight": "late_blight",
    "tomato early blight": "early_blight", "tomato leaf mold": "leaf_mold",
    "tomato bacterial leaf spot": "bacterial_spot", "tomato septoria leaf spot": "septoria_leaf_spot",
    "tomato mosaic virus": "mosaic_virus",
}
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def images(folder):
    return sorted(p for p in Path(folder).rglob("*") if p.suffix.lower() in IMG_EXT)


def bucket(key, *weights):
    """Deterministic split index from a string key."""
    x = int(hashlib.md5(key.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    acc = 0.0
    for i, w in enumerate(weights):
        acc += w
        if x < acc:
            return i
    return len(weights) - 1


def phash(path):
    try:
        with Image.open(path) as im:
            return str(path), str(imagehash.phash(im.convert("RGB")))
    except Exception:
        return str(path), None


def crop_box(args):
    src, box, out = args
    try:
        with Image.open(src) as im:
            im = im.convert("RGB")
            x0, y0, x1, y1 = box
            pw, ph = (x1 - x0) * 0.08, (y1 - y0) * 0.08  # small context margin
            x0, y0 = max(0, x0 - pw), max(0, y0 - ph)
            x1, y1 = min(im.width, x1 + pw), min(im.height, y1 + ph)
            if x1 - x0 < 40 or y1 - y0 < 40:
                return None
            im.crop((int(x0), int(y0), int(x1), int(y1))).save(out, quality=92)
            return out
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="/workspace/agri/raw")
    ap.add_argument("--out", default="/workspace/agri/data")
    ap.add_argument("--workers", type=int, default=96)
    args = ap.parse_args()
    raw, out = Path(args.raw), Path(args.out)
    (out / "crops").mkdir(parents=True, exist_ok=True)
    rows = []  # dicts: path,label,source,domain,split,group

    def add(path, label, source, domain, split, group=None):
        rows.append(dict(path=str(path), label=label, source=source, domain=domain,
                         split=split, group=group or str(path)))

    # ---- PlantVillage (lab): 90/5/5
    for folder, label in PV.items():
        for p in images(raw / "plantvillage/raw/color" / folder):
            add(p, label, "plantvillage", "lab", ["train", "val", "test_lab"][bucket(p.name, .90, .05, .05)])

    # ---- PlantDoc classification folders (field): official test held out
    for split_dir, split in [("train", "train"), ("test", "test_field")]:
        for folder, label in PD.items():
            for p in images(raw / "plantdoc" / split_dir / folder):
                s = split if split == "test_field" else ["train", "val"][bucket(p.name, .88, .12)]
                add(p, label, "plantdoc", "field", s, group="pd:" + p.stem)

    # ---- PlantDoc object-detection boxes -> leaf crops (field); non-tomato leaves -> negatives
    jobs, meta = [], []
    for csv_name, img_dir, split in [("train_labels.csv", "TRAIN", "train"), ("test_labels.csv", "TEST", "test_field")]:
        for i, r in enumerate(csv.DictReader(open(raw / "plantdoc_od" / csv_name))):
            src = raw / "plantdoc_od" / img_dir / r["filename"]
            if not src.exists():
                continue
            label = PD.get(r["class"])
            if label is None and "omato" in r["class"]:
                continue
            label = label or "no_tomato_leaf"
            if label == "no_tomato_leaf" and bucket(f"{r['filename']}{i}", .35, .65) == 1:
                continue  # keep ~35% of other-plant leaves so negatives don't dominate
            box = tuple(float(r[k]) for k in ("xmin", "ymin", "xmax", "ymax"))
            dst = out / "crops" / f"pdod_{split}_{i}.jpg"
            s = split if split == "test_field" else ["train", "val"][bucket(r["filename"], .88, .12)]
            jobs.append((str(src), box, str(dst)))
            meta.append((label, s, "pd:" + Path(r["filename"]).stem))
    with ProcessPoolExecutor(args.workers) as ex:
        for res, (label, s, group) in zip(ex.map(crop_box, jobs, chunksize=32), meta):
            if res:
                add(res, label, "plantdoc_od", "field" if label != "no_tomato_leaf" else "neg", s, group)

    # ---- Tomato-Village (field): use its own train/val/test
    base = raw / "tomato_village/Variant-a(Multiclass Classification)"
    for split_dir, split in [("train", "train"), ("val", "val"), ("test", "test_field")]:
        for folder, label in TV.items():
            for p in images(base / split_dir / folder):
                add(p, label, "tomato_village", "field", split)

    # ---- PlantWild (in the wild): tomato classes 70/12/18; other plants -> hard negatives
    pw_root = raw / "plantwild/plantwild_v2"
    for d in sorted(pw_root.iterdir()):
        if not d.is_dir():
            continue
        label = PW.get(d.name)
        files = images(d)
        if label is None:
            if d.name.startswith("tomato"):
                continue
            files = files[:30]  # a few per other-plant class keeps negatives balanced
            label = "no_tomato_leaf"
        for p in files:
            add(p, label, "plantwild", "field" if label != "no_tomato_leaf" else "neg",
                ["train", "val", "test_field"][bucket(p.name + d.name, .70, .12, .18)])

    # ---- Negatives from the drone's own camera and everyday objects
    for p in images(raw / "drone_negatives"):
        add(p, "no_tomato_leaf", "drone_camera", "neg", ["train", "val", "test_field"][bucket(p.name, .70, .10, .20)])
    imn = images(raw / "imagenette/train")
    random.Random(0).shuffle(imn)
    for p in imn[:2500]:
        add(p, "no_tomato_leaf", "imagenette", "neg", ["train", "val"][bucket(p.name, .9, .1)])
    imn_val = images(raw / "imagenette/val")
    random.Random(1).shuffle(imn_val)
    for p in imn_val[:300]:
        add(p, "no_tomato_leaf", "imagenette", "neg", "test_field")

    # ---- Leak control: drop non-test field/neg images that near-duplicate any test image
    check = [r for r in rows if r["domain"] in ("field", "neg")]
    with ProcessPoolExecutor(args.workers) as ex:
        hashes = dict(ex.map(phash, [r["path"] for r in check], chunksize=64))
    test_h = [imagehash.hex_to_hash(hashes[r["path"]]) for r in check
              if r["split"] == "test_field" and hashes.get(r["path"])]
    test_groups = {r["group"] for r in rows if r["split"] == "test_field"}
    dropped = Counter()
    keep = []
    for r in rows:
        h = hashes.get(r["path"])
        if r["split"] != "test_field" and r["group"] in test_groups:
            dropped["same source image as a test item"] += 1
            continue
        if r["split"] != "test_field" and h:
            hh = imagehash.hex_to_hash(h)
            if any(hh - t <= HASH_DIST for t in test_h):
                dropped["near-duplicate of a test image"] += 1
                continue
        if r["domain"] in ("field", "neg") and h is None:
            dropped["unreadable"] += 1
            continue
        keep.append(r)

    with open(out / "manifest.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["path", "label", "source", "domain", "split", "group"])
        w.writeheader()
        w.writerows(keep)

    summary = {"classes": CLASSES, "dropped": dict(dropped), "splits": {}}
    by = defaultdict(Counter)
    for r in keep:
        by[r["split"]][r["label"]] += 1
        by[r["split"] + ":source"][r["source"]] += 1
    for k, v in sorted(by.items()):
        summary["splits"][k] = dict(sorted(v.items()))
    json.dump(summary, open(out / "summary.json", "w"), indent=2)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
