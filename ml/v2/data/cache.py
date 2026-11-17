#!/usr/bin/env python3
"""Pre-resize every manifest image to <=640 px (longest side) so training is GPU-bound.

Writes data/cache/<md5>.jpg and data/manifest_cached.csv (same rows, path -> cached file).
"""
import csv
import hashlib
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from PIL import Image

DATA = Path(sys.argv[1] if len(sys.argv) > 1 else "/workspace/agri/data")
CACHE = DATA / "cache"
MAX_SIDE = 640


def resize(path):
    dst = CACHE / (hashlib.md5(path.encode()).hexdigest() + ".jpg")
    if dst.exists():
        return str(dst)
    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
            im.save(dst, quality=93)
        return str(dst)
    except Exception:
        return None


if __name__ == "__main__":
    CACHE.mkdir(exist_ok=True)
    rows = list(csv.DictReader(open(DATA / "manifest.csv")))
    with ProcessPoolExecutor(110) as ex:
        cached = list(ex.map(resize, [r["path"] for r in rows], chunksize=64))
    kept = []
    for r, c in zip(rows, cached):
        if c:
            kept.append({**r, "orig": r["path"], "path": c})
    with open(DATA / "manifest_cached.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(kept[0].keys()))
        w.writeheader()
        w.writerows(kept)
    print(f"cached {len(kept)}/{len(rows)} images")
