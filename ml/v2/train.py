#!/usr/bin/env python3
"""Train a teacher (large backbone) or distil a student (Pi-sized) for tomato leaf disease.

Teacher:  python train.py --model eva02_large_patch14_448.mim_m38m_ft_in22k_in1k --res 448 --name eva02L
Student:  python train.py --model mobilenetv3_large_100.ra_in1k --res 256 --name mnv3L \
              --teachers runs/eva02L/best.pt runs/dinov2L/best.pt

Students use "consistent" distillation (Beyer et al. 2022): teacher and student see the same
augmented image; the target is the teacher-ensemble's softened prediction.
Model selection uses the val split only; the held-out field test is scored once at the end.
"""
import argparse
import json
import math
import time
from pathlib import Path

import numpy as np
import timm
import torch
import torch.nn.functional as F
from timm.data import Mixup
from timm.loss import SoftTargetCrossEntropy
from timm.optim import create_optimizer_v2
from timm.utils import ModelEmaV3
from torch.utils.data import DataLoader

from common import (CLASSES, ManifestDataset, PasteOnBackground, balanced_sampler, eval_transform,
                    metrics, read_manifest, train_transform)


def build(model_name, res, pretrained=True):
    kw = {"num_classes": len(CLASSES), "pretrained": pretrained}
    if "vit" in model_name or "eva" in model_name:
        kw["img_size"] = res
    return timm.create_model(model_name, **kw)


@torch.no_grad()
def predict(model, loader, device, tta=True):
    model.eval()
    probs = []
    for x, _ in loader:
        x = x.to(device, non_blocking=True)
        with torch.autocast("cuda", dtype=torch.bfloat16):
            logit = model(x).float()
            if tta:
                logit = (logit + model(torch.flip(x, dims=[3])).float()) / 2
        probs.append(logit.cpu())
    return torch.cat(probs)


def load_teacher(path, device):
    ck = torch.load(path, map_location="cpu", weights_only=False)
    m = build(ck["model_name"], ck["res"], pretrained=False)
    m.load_state_dict(ck["state_dict"])
    return m.to(device).eval(), ck["res"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--res", type=int, default=448)
    ap.add_argument("--name", required=True)
    ap.add_argument("--manifest", default="/workspace/agri/data/manifest_cached.csv")
    ap.add_argument("--epochs", type=int, default=15)
    ap.add_argument("--epoch-size", type=int, default=24000)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--lr", type=float, default=4e-5)
    ap.add_argument("--layer-decay", type=float, default=0.8)
    ap.add_argument("--wd", type=float, default=0.05)
    ap.add_argument("--workers", type=int, default=32)
    ap.add_argument("--teachers", nargs="*", default=[])
    ap.add_argument("--kd-temp", type=float, default=2.0)
    ap.add_argument("--kd-weight", type=float, default=0.8, help="share of the loss from the teacher (rest: true labels)")
    ap.add_argument("--init-from", default="", help="start from this checkpoint (e.g. a short fine-tune of a student)")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.allow_tf32 = True
    device = "cuda"
    out = Path("/workspace/agri/runs") / args.name
    out.mkdir(parents=True, exist_ok=True)
    log = open(out / "log.txt", "a")

    def say(msg):
        print(msg, flush=True)
        log.write(msg + "\n")
        log.flush()

    train_rows = read_manifest(args.manifest, {"train"})
    val_rows = read_manifest(args.manifest, {"val"})
    test_rows = read_manifest(args.manifest, {"test_field"})
    backgrounds = [r["path"] for r in train_rows if r["domain"] in ("neg", "field")]
    distil = bool(args.teachers)

    teachers = []  # (model, native resolution)
    for tp in args.teachers:
        teachers.append(load_teacher(tp, device))
    # One augmented view at the largest teacher resolution; each teacher and the student get it
    # resized to their own input size, so all of them judge the identical crop.
    gen_res = max(r for _, r in teachers) if distil else args.res

    train_ds = ManifestDataset(train_rows, train_transform(gen_res), PasteOnBackground(backgrounds))
    train_dl = DataLoader(train_ds, batch_size=args.batch, sampler=balanced_sampler(train_rows, epoch_size=args.epoch_size),
                          num_workers=args.workers, pin_memory=True, drop_last=True, persistent_workers=True,
                          prefetch_factor=4)
    val_dl = DataLoader(ManifestDataset(val_rows, eval_transform(args.res)), batch_size=128, num_workers=16)
    test_dl = DataLoader(ManifestDataset(test_rows, eval_transform(args.res)), batch_size=128, num_workers=16)

    model = build(args.model, args.res).to(device)
    if args.init_from:
        model.load_state_dict(torch.load(args.init_from, map_location="cpu", weights_only=False)["state_dict"])
    if "convnext" in args.model or "efficientnet" in args.model or "mobilenet" in args.model:
        model = model.to(memory_format=torch.channels_last)
    ld = args.layer_decay if hasattr(model, "blocks") or hasattr(model, "stages") else None
    opt = create_optimizer_v2(model, "adamw", lr=args.lr, weight_decay=args.wd, layer_decay=ld)
    steps = args.epochs * (args.epoch_size // args.batch)
    warm = max(1, int(0.05 * steps))
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: (s + 1) / warm if s < warm else 0.5 * (1 + math.cos(math.pi * (s - warm) / max(1, steps - warm))))
    ema = ModelEmaV3(model, decay=0.9995 if steps > 8000 else 0.999)
    mixup = Mixup(mixup_alpha=0.4, cutmix_alpha=1.0, prob=0.6, label_smoothing=0.1, num_classes=len(CLASSES))
    soft_ce = SoftTargetCrossEntropy()
    say(f"{args.name}: {args.model} @ {args.res}px | params {sum(p.numel() for p in model.parameters())/1e6:.1f}M | "
        f"train {len(train_rows)} val {len(val_rows)} test {len(test_rows)} | steps {steps} | distil={distil}")

    best, step = -1.0, 0
    for epoch in range(1, args.epochs + 1):
        model.train()
        t0, seen, run_loss = time.time(), 0, 0.0
        for x, y in train_dl:
            x, y = x.to(device, non_blocking=True), y.to(device, non_blocking=True)
            x, y_soft = mixup(x, y)
            with torch.autocast("cuda", dtype=torch.bfloat16):
                if distil:
                    with torch.no_grad():
                        fit = lambda r: x if r == x.shape[-1] else F.interpolate(
                            x, size=r, mode="bilinear", antialias=True, align_corners=False)
                        t_prob = torch.stack([F.softmax(t(fit(r)).float() / args.kd_temp, dim=1)
                                              for t, r in teachers]).mean(0)
                    xs = F.interpolate(x, size=args.res, mode="bilinear", antialias=True, align_corners=False)
                    logits = model(xs).float()
                    kd = F.kl_div(F.log_softmax(logits / args.kd_temp, dim=1), t_prob, reduction="batchmean")
                    loss = args.kd_weight * kd * args.kd_temp ** 2 + (1 - args.kd_weight) * soft_ce(logits, y_soft)
                else:
                    loss = soft_ce(model(x).float(), y_soft)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            sched.step()
            ema.update(model, step=step)
            step += 1
            seen += x.size(0)
            run_loss += loss.item() * x.size(0)

        val_logits = predict(ema.module, val_dl, device)
        vm = metrics([r["y"] for r in val_rows], val_logits.argmax(1).tolist(), val_rows)
        score = 0.85 * vm["field_macro_f1"] + 0.15 * vm["no_leaf_recall"]
        say(f"epoch {epoch}/{args.epochs} loss {run_loss/seen:.4f} | val field macro-F1 {vm['field_macro_f1']:.4f} "
            f"acc {vm['field_accuracy']:.4f} no-leaf recall {vm['no_leaf_recall']:.4f} | "
            f"{seen/(time.time()-t0):.0f} img/s")
        if score > best:
            best = score
            torch.save({"state_dict": ema.module.state_dict(), "model_name": args.model, "res": args.res,
                        "classes": CLASSES, "epoch": epoch, "val": vm}, out / "best.pt")
            np.save(out / "val_logits.npy", val_logits.numpy())

    # One-shot held-out evaluation with the best checkpoint.
    ck = torch.load(out / "best.pt", map_location="cpu", weights_only=False)
    final = build(args.model, args.res, pretrained=False).to(device)
    final.load_state_dict(ck["state_dict"])
    test_logits = predict(final, test_dl, device)
    np.save(out / "test_logits.npy", test_logits.numpy())
    tm = metrics([r["y"] for r in test_rows], test_logits.argmax(1).tolist(), test_rows)
    report = {"name": args.name, "model": args.model, "res": args.res, "best_epoch": ck["epoch"],
              "val": ck["val"], "test_field": tm}
    json.dump(report, open(out / "report.json", "w"), indent=2)
    say("HELD-OUT FIELD TEST: " + json.dumps({k: tm[k] for k in ("field_accuracy", "field_macro_f1",
        "no_leaf_recall", "false_disease_rate_on_negatives")}))
    say("DONE")


if __name__ == "__main__":
    main()
