"""Test the trained model on the Roboflow tomato photos (real-world check).

Usage:
    python ml/eval_roboflow.py --data "C:\\datasets\\archive"
"""
import argparse
from collections import Counter
from pathlib import Path

import torch
from PIL import Image
from torchvision import models, transforms

MEAN = [0.485, 0.456, 0.406]
STD = [0.229, 0.224, 0.225]

# Roboflow class id -> our model's class name (None = model doesn't know it)
RF = {0: "Tomato___Bacterial_spot", 1: "Tomato___Early_blight",
      2: "Tomato___healthy", 3: "Tomato___Late_blight",
      4: "Tomato___Leaf_Mold", 5: None, 6: None}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True)
    p.add_argument("--model", default="ml/model.pt")
    args = p.parse_args()

    ckpt = torch.load(args.model, map_location="cpu")
    classes = ckpt["classes"]
    model = models.mobilenet_v3_small(weights=None)
    model.classifier[3] = torch.nn.Linear(model.classifier[3].in_features, len(classes))
    model.load_state_dict(ckpt["model"])
    model.eval()

    tf = transforms.Compose([
        transforms.Resize((224, 224)),
        transforms.ToTensor(),
        transforms.Normalize(MEAN, STD),
    ])

    n = skipped = strict_ok = lenient_ok = 0
    per_class = {}
    wrong = []

    for split in ["valid", "test"]:
        img_dir = Path(args.data) / split / "images"
        lbl_dir = Path(args.data) / split / "labels"
        for lbl in sorted(lbl_dir.glob("*.txt")):
            imgs = list(img_dir.glob(lbl.stem + ".*"))
            if not imgs:
                skipped += 1
                continue
            counts = Counter()
            for line in lbl.read_text().splitlines():
                if line.strip():
                    name = RF.get(int(line.split()[0]))
                    if name:
                        counts[name] += 1
            if not counts:          # only Target_Spot / black spot -> skip
                skipped += 1
                continue

            truth_set = set(counts)
            truth = counts.most_common(1)[0][0]

            img = Image.open(imgs[0]).convert("RGB")
            with torch.no_grad():
                probs = torch.softmax(model(tf(img).unsqueeze(0)), dim=1)[0]
            pred = classes[int(probs.argmax())]
            conf = float(probs.max())

            n += 1
            s_ok, l_ok = pred == truth, pred in truth_set
            strict_ok += s_ok
            lenient_ok += l_ok
            c = per_class.setdefault(truth, [0, 0])
            c[0] += 1
            c[1] += s_ok
            if not l_ok:
                wrong.append((imgs[0].name[:22], sorted(truth_set), pred, round(conf, 2)))

    print(f"\nImages scored: {n}   skipped: {skipped}")
    if n == 0:
        print("Nothing scored - check the --data path.")
        return
    print(f"Strict accuracy  (matches main labelled class): {strict_ok/n:.3f}")
    print(f"Lenient accuracy (matches any labelled class):  {lenient_ok/n:.3f}")
    print("\nStrict, per main class:")
    for k, (tot, ok) in sorted(per_class.items()):
        print(f"  {k:30s} {ok}/{tot}")
    print("\nWrong even under lenient scoring:")
    for w in wrong:
        print("  ", w)


if __name__ == "__main__":
    main()