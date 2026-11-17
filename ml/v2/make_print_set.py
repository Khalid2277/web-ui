#!/usr/bin/env python3
"""Build a printable live-demo set from the HELD-OUT field test split (never trained on).

2 random photos per disease class with field data + 4 non-tomato plant leaves, fixed seed
(no cherry-picking). Writes print_set.pdf (2 photos per A4 page, labelled only with an ID)
and answer_key.csv (ID -> true class, source).
"""
import csv
import random
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path("/workspace/agri/print_set")
OUT.mkdir(exist_ok=True)
rows = [r for r in csv.DictReader(open("/workspace/agri/data/manifest_cached.csv")) if r["split"] == "test_field"]
by_class = defaultdict(list)
for r in rows:
    if r["label"] != "no_tomato_leaf" and r["source"] in ("plantdoc", "plantwild", "tomato_village"):
        by_class[r["label"]].append(r)
negs = [r for r in rows if r["label"] == "no_tomato_leaf" and r["source"] == "plantwild"]

rng = random.Random(2026)
picks = []
for label in sorted(by_class):
    picks += rng.sample(by_class[label], min(2, len(by_class[label])))
picks += rng.sample(negs, 4)
rng.shuffle(picks)

# A4 at 150 dpi = 1240 x 1754 px; 2 photos per page.
W, H, M = 1240, 1754, 70
try:
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 40)
except OSError:
    font = ImageFont.load_default()
pages, key = [], []
for i in range(0, len(picks), 2):
    page = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(page)
    for j, r in enumerate(picks[i:i + 2]):
        pid = f"T{i + j + 1:02d}"
        img = Image.open(r["orig"]).convert("RGB")
        box_h = (H - 3 * M) // 2 - 60
        # Scale up or down to fill the half page, so the camera sees a leaf-sized image at 30-60 cm.
        s = min((W - 2 * M) / img.width, box_h / img.height)
        img = img.resize((int(img.width * s), int(img.height * s)), Image.Resampling.LANCZOS)
        y = M + j * (box_h + 60 + M)
        page.paste(img, ((W - img.width) // 2, y + 60))
        d.text((M, y), pid, fill="black", font=font)
        key.append({"id": pid, "truth": r["label"], "source": r["source"], "file": Path(r["orig"]).name})
    pages.append(page)
pages[0].save(OUT / "print_set.pdf", save_all=True, append_images=pages[1:], resolution=150)
with open(OUT / "answer_key.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["id", "truth", "source", "file"])
    w.writeheader()
    w.writerows(key)
print(f"{len(picks)} photos on {len(pages)} pages")
