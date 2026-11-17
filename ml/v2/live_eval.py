#!/usr/bin/env python3
"""Score live test-mode captures from the drone console.

During the live test, each item (printed photo, real leaf, empty scene) is captured in the
console with its true label selected. This script reads those records and reports accuracy,
confusions, false-disease rate on negatives and latency.

Run on the Pi:   python3 ml/v2/live_eval.py
or on a laptop:  python3 ml/v2/live_eval.py --log path/to/detections.jsonl
"""
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

DEFAULT_LOG = Path(__file__).resolve().parents[2] / "raspberry_pi/dashboard/captures/detections.jsonl"
NOT_DISEASE = {"healthy", "no_tomato_leaf"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=str(DEFAULT_LOG))
    ap.add_argument("--since", type=float, default=0, help="only captures after this unix time")
    ap.add_argument("--model", default=None, help="only captures made with this model name")
    args = ap.parse_args()

    rows = [json.loads(l) for l in open(args.log) if l.strip()]
    rows = [r for r in rows if r.get("truth") and r["t"] >= args.since and (not args.model or r.get("model") == args.model)]
    if not rows:
        print("No labelled test captures found. In the console, pick the true label under "
              "'Test mode' before pressing Capture & log detection.")
        return

    leaf = [r for r in rows if r["truth"] != "no_tomato_leaf"]
    neg = [r for r in rows if r["truth"] == "no_tomato_leaf"]
    correct = lambda r: r["label"] == r["truth"]
    answered = [r for r in leaf if r.get("status") != "uncertain"]
    by_kind = defaultdict(list)
    for r in rows:
        item = (r.get("test_item") or "").lower()
        kind = "printed photo" if "print" in item else "real leaf/plant" if any(
            w in item for w in ("real", "leaf", "plant")) else "other / unspecified"
        by_kind[kind].append(correct(r))

    report = {
        "captures": len(rows),
        "leaf_items": len(leaf),
        "accuracy_on_leaves": round(sum(map(correct, leaf)) / len(leaf), 3) if leaf else None,
        "uncertain_rate": round(1 - len(answered) / len(leaf), 3) if leaf else None,
        "accuracy_when_not_uncertain": round(sum(map(correct, answered)) / len(answered), 3) if answered else None,
        "negative_items": len(neg),
        "false_disease_rate_on_negatives": round(sum(r["label"] not in NOT_DISEASE for r in neg) / len(neg), 3) if neg else None,
        "accuracy_by_item_type": {k: f"{sum(v)}/{len(v)}" for k, v in sorted(by_kind.items())},
        "median_latency_ms": sorted(r.get("ms") or 0 for r in rows)[len(rows) // 2],
        "mean_leaves_per_capture": round(sum(len(r.get("leaves") or []) for r in rows) / len(rows), 2),
    }
    per_class = defaultdict(lambda: [0, 0])
    confusions = Counter()
    for r in rows:
        per_class[r["truth"]][0] += 1
        per_class[r["truth"]][1] += correct(r)
        if not correct(r):
            confusions[(r["truth"], r["label"])] += 1
    report["per_class"] = {k: f"{ok}/{n}" for k, (n, ok) in sorted(per_class.items())}
    report["top_confusions"] = [f"{t} -> {p}: {n}" for (t, p), n in confusions.most_common(8)]
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
