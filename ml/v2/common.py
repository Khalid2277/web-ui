"""Shared data loading, augmentation and metrics for ml/v2."""
import csv
import random
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageFilter
from sklearn.metrics import f1_score
from torch.utils.data import Dataset, WeightedRandomSampler
from torchvision.transforms import v2 as T

CLASSES = [
    "bacterial_spot", "early_blight", "late_blight", "leaf_mold", "septoria_leaf_spot",
    "spider_mites", "target_spot", "yellow_leaf_curl_virus", "mosaic_virus", "healthy",
    "leaf_miner", "spotted_wilt_virus", "magnesium_deficiency", "nitrogen_deficiency",
    "potassium_deficiency", "no_tomato_leaf",
]
NO_LEAF = CLASSES.index("no_tomato_leaf")
HEALTHY = CLASSES.index("healthy")
NOT_DISEASE = {NO_LEAF, HEALTHY}
MEAN, STD = (0.485, 0.456, 0.406), (0.229, 0.224, 0.225)


def read_manifest(path, splits):
    rows = [r for r in csv.DictReader(open(path)) if r["split"] in splits]
    for r in rows:
        r["y"] = CLASSES.index(r["label"])
    return rows


def segmented_path(orig):
    """PlantVillage raw/color/<cls>/<name>.JPG -> raw/segmented/<cls>/<name>_final_masked.jpg"""
    p = Path(orig)
    if "/raw/color/" not in str(p):
        return None
    seg = Path(str(p.parent).replace("/raw/color/", "/raw/segmented/")) / (p.stem + "_final_masked.jpg")
    return seg if seg.exists() else None


class PasteOnBackground:
    """Paste a segmented lab leaf onto a real background (lab -> field gap trick)."""

    def __init__(self, backgrounds, p=0.6):
        self.backgrounds, self.p = backgrounds, p

    def __call__(self, leaf_rgb, seg_path):
        if seg_path is None or not self.backgrounds or random.random() > self.p:
            return leaf_rgb
        seg = np.asarray(Image.open(seg_path).convert("RGB"))
        mask = (seg.max(axis=2) > 18).astype(np.uint8) * 255
        mask_img = Image.fromarray(mask).filter(ImageFilter.GaussianBlur(1.2))
        bg = Image.open(random.choice(self.backgrounds)).convert("RGB")
        side = random.randint(300, 520)
        bg = T.RandomResizedCrop(side, scale=(0.3, 1.0))(bg)
        scale = random.uniform(0.55, 1.0) * side / 256
        size = (int(256 * scale), int(256 * scale))
        leaf = Image.fromarray(seg).resize(size, Image.Resampling.BILINEAR)
        mask_img = mask_img.resize(size, Image.Resampling.BILINEAR)
        ox = random.randint(0, max(0, side - size[0]))
        oy = random.randint(0, max(0, side - size[1]))
        bg.paste(leaf, (ox, oy), mask_img)
        return bg


def train_transform(res):
    return T.Compose([
        T.RandomResizedCrop(res, scale=(0.30, 1.0), ratio=(0.75, 1.33), antialias=True),
        T.RandomHorizontalFlip(), T.RandomVerticalFlip(),
        T.RandomApply([T.RandomRotation(30)], p=0.4),
        T.TrivialAugmentWide(),
        T.RandomApply([T.ColorJitter(0.35, 0.35, 0.3, 0.04)], p=0.6),
        T.RandomApply([T.GaussianBlur(5, sigma=(0.1, 2.0))], p=0.25),   # drone motion / focus blur
        T.RandomGrayscale(p=0.03),
        T.ToImage(), T.ToDtype(torch.float32, scale=True),
        T.Normalize(MEAN, STD),
        T.RandomErasing(p=0.2, scale=(0.02, 0.12)),
    ])


def eval_transform(res):
    return T.Compose([
        T.Resize(res, antialias=True), T.CenterCrop(res),
        T.ToImage(), T.ToDtype(torch.float32, scale=True), T.Normalize(MEAN, STD),
    ])


class ManifestDataset(Dataset):
    def __init__(self, rows, transform, paste=None):
        self.rows, self.transform, self.paste = rows, transform, paste

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, i):
        r = self.rows[i]
        img = Image.open(r["path"]).convert("RGB")
        if self.paste is not None and r["source"] == "plantvillage":
            img = self.paste(img, segmented_path(r["orig"]))
        return self.transform(img), r["y"]


def balanced_sampler(rows, field_boost=3.0, epoch_size=None):
    """Weight ~ 1/sqrt(class count) per domain, with field images boosted over lab."""
    counts = Counter((r["domain"], r["y"]) for r in rows)
    w = []
    for r in rows:
        base = 1.0 / np.sqrt(counts[(r["domain"], r["y"])])
        w.append(base * (field_boost if r["domain"] == "field" else 1.0))
    return WeightedRandomSampler(w, num_samples=epoch_size or len(rows), replacement=True)


def metrics(y_true, y_pred, rows):
    """Headline metrics for the held-out field test."""
    y_true, y_pred = np.asarray(y_true), np.asarray(y_pred)
    leaf = y_true != NO_LEAF
    present = sorted(set(y_true[leaf].tolist()))
    out = {
        "n": int(len(y_true)),
        "leaf_images": int(leaf.sum()),
        "negative_images": int((~leaf).sum()),
        "field_accuracy": float((y_true[leaf] == y_pred[leaf]).mean()) if leaf.any() else None,
        "field_macro_f1": float(f1_score(y_true[leaf], y_pred[leaf], labels=present, average="macro", zero_division=0)),
        "no_leaf_recall": float((y_pred[~leaf] == NO_LEAF).mean()) if (~leaf).any() else None,
        "false_disease_rate_on_negatives": float(np.mean([p not in NOT_DISEASE for p in y_pred[~leaf]])) if (~leaf).any() else None,
        "missed_leaf_rate": float((y_pred[leaf] == NO_LEAF).mean()) if leaf.any() else None,
    }
    per_class = f1_score(y_true, y_pred, labels=present, average=None, zero_division=0)
    out["per_class_f1"] = {CLASSES[c]: round(float(f), 3) for c, f in zip(present, per_class)}
    by_src = defaultdict(list)
    for t, p, r in zip(y_true, y_pred, rows):
        by_src[r["source"]].append(t == p)
    out["accuracy_by_source"] = {k: round(float(np.mean(v)), 3) for k, v in sorted(by_src.items())}
    return out
