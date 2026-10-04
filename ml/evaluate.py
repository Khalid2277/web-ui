"""Evaluate a trained model on a folder of class subfolders.

Usage:
    python ml/evaluate.py --data "C:\\datasets\\tomato_pv\\Dataset\\test"
"""
import argparse

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import torch
from sklearn.metrics import classification_report, confusion_matrix, ConfusionMatrixDisplay
from torch.utils.data import DataLoader
from torchvision import datasets, models, transforms

MEAN = [0.485, 0.456, 0.406]
STD = [0.229, 0.224, 0.225]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True, help="folder with one subfolder per class")
    p.add_argument("--model", default="ml/model.pt")
    p.add_argument("--out", default="ml/confusion_matrix.png")
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
    ds = datasets.ImageFolder(args.data, tf)
    if ds.classes != classes:
        print("WARNING: folder classes differ from model classes")
        print("  folder:", ds.classes)
        print("  model: ", classes)
    dl = DataLoader(ds, batch_size=64)

    y_true, y_pred = [], []
    with torch.no_grad():
        for x, y in dl:
            y_pred += model(x).argmax(1).tolist()
            y_true += y.tolist()

    print(f"\nImages: {len(ds)}")
    print(classification_report(y_true, y_pred, target_names=ds.classes, digits=3))

    cm = confusion_matrix(y_true, y_pred)
    fig, ax = plt.subplots(figsize=(8, 7))
    ConfusionMatrixDisplay(cm, display_labels=[c.replace("Tomato___", "") for c in ds.classes]).plot(
        ax=ax, xticks_rotation=45, colorbar=False)
    plt.tight_layout()
    plt.savefig(args.out, dpi=150)
    print("Saved confusion matrix to", args.out)


if __name__ == "__main__":
    main()