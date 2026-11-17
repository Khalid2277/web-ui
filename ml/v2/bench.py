#!/usr/bin/env python3
"""Throughput check: data loader vs GPU, to find the training bottleneck."""
import sys
import time

import torch
from torch.utils.data import DataLoader

from common import ManifestDataset, PasteOnBackground, balanced_sampler, read_manifest, train_transform
from train import build

MANIFEST = "/workspace/agri/data/manifest_cached.csv"


def bench_loader(workers, res=448):
    rows = read_manifest(MANIFEST, {"train"})
    bg = [r["path"] for r in rows if r["domain"] in ("neg", "field")]
    dl = DataLoader(ManifestDataset(rows, train_transform(res), PasteOnBackground(bg)), batch_size=32,
                    sampler=balanced_sampler(rows, epoch_size=32 * 60), num_workers=workers, prefetch_factor=4)
    it = iter(dl)
    next(it)
    t, n = time.time(), 0
    for x, _ in it:
        n += x.size(0)
    print(f"dataloader {workers} workers @{res}: {n / (time.time() - t):.0f} img/s", flush=True)


def bench_gpu(name, res, bs=32, compile_=False):
    m = build(name, res).cuda().train()
    if "convnext" in name:
        m = m.to(memory_format=torch.channels_last)
    if compile_:
        m = torch.compile(m)
    opt = torch.optim.AdamW(m.parameters(), 1e-5)
    x = torch.randn(bs, 3, res, res, device="cuda")
    y = torch.randint(0, 16, (bs,), device="cuda")
    for i in range(15):
        if i == 5:
            torch.cuda.synchronize()
            t = time.time()
        with torch.autocast("cuda", dtype=torch.bfloat16):
            loss = torch.nn.functional.cross_entropy(m(x).float(), y)
        opt.zero_grad()
        loss.backward()
        opt.step()
    torch.cuda.synchronize()
    tag = name.split(".")[0] + (" +compile" if compile_ else "")
    print(f"GPU {tag} @{res}: {bs * 10 / (time.time() - t):.0f} img/s train, "
          f"{torch.cuda.max_memory_allocated() / 2**30:.0f} GiB", flush=True)
    del m, opt
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()


if __name__ == "__main__":
    what = sys.argv[1]
    if what == "loader":
        for w in (32, 100):
            bench_loader(w)
    else:
        bench_gpu("eva02_large_patch14_448.mim_m38m_ft_in22k_in1k", 448)
        bench_gpu("vit_large_patch14_dinov2.lvd142m", 448)
        bench_gpu("convnextv2_large.fcmae_ft_in22k_in1k_384", 384)
