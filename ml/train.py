"""Train a MobileNetV3-Small tomato leaf classifier.

Usage (smoke test, 1 epoch):
    python ml/train.py --data "C:\\datasets\\tomato_pv\\Dataset" --epochs 1
"""
import argparse
import time

import torch
import torch.nn as nn
from torch.utils.data import DataLoader
from torchvision import datasets, models, transforms

MEAN = [0.485, 0.456, 0.406]
STD = [0.229, 0.224, 0.225]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True, help="folder containing train/ val/ test/")
    p.add_argument("--epochs", type=int, default=10)
    p.add_argument("--batch", type=int, default=32)
    p.add_argument("--lr", type=float, default=3e-4)
    p.add_argument("--out", default="ml/model.pt")
    args = p.parse_args()

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    workers = 2 if dev == "cuda" else 0
    print("Device:", dev)

    train_tf = transforms.Compose([
        transforms.RandomResizedCrop(224, scale=(0.4, 1.0)),
        transforms.RandomHorizontalFlip(),
        transforms.RandomVerticalFlip(),
        transforms.ColorJitter(0.3, 0.3, 0.3, 0.05),
        transforms.ToTensor(),
        transforms.Normalize(MEAN, STD),
    ])
    eval_tf = transforms.Compose([
        transforms.Resize((224, 224)),
        transforms.ToTensor(),
        transforms.Normalize(MEAN, STD),
    ])

    train_ds = datasets.ImageFolder(f"{args.data}/train", train_tf)
    val_ds = datasets.ImageFolder(f"{args.data}/val", eval_tf)
    print("Classes:", train_ds.classes)
    print(f"Train images: {len(train_ds)}  Val images: {len(val_ds)}")

    train_dl = DataLoader(train_ds, batch_size=args.batch, shuffle=True, num_workers=workers)
    val_dl = DataLoader(val_ds, batch_size=64, num_workers=workers)

    model = models.mobilenet_v3_small(weights="DEFAULT")
    model.classifier[3] = nn.Linear(model.classifier[3].in_features, len(train_ds.classes))
    model.to(dev)

    opt = torch.optim.AdamW(model.parameters(), lr=args.lr)
    loss_fn = nn.CrossEntropyLoss()
    best = 0.0

    for epoch in range(1, args.epochs + 1):
        t0 = time.time()
        model.train()
        total_loss = 0.0
        for x, y in train_dl:
            x, y = x.to(dev), y.to(dev)
            opt.zero_grad()
            loss = loss_fn(model(x), y)
            loss.backward()
            opt.step()
            total_loss += loss.item() * x.size(0)

        model.eval()
        correct = 0
        with torch.no_grad():
            for x, y in val_dl:
                pred = model(x.to(dev)).argmax(1).cpu()
                correct += (pred == y).sum().item()
        acc = correct / len(val_ds)
        print(f"epoch {epoch}/{args.epochs}  loss {total_loss/len(train_ds):.4f}  "
              f"val acc {acc:.4f}  ({time.time()-t0:.0f}s)")

        if acc > best:
            best = acc
            torch.save({"model": model.state_dict(), "classes": train_ds.classes}, args.out)
            print("  saved best model")

    print(f"Done. Best val acc: {best:.4f}")


if __name__ == "__main__":
    main()