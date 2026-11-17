#!/usr/bin/env python3
"""Learned environmental model, trained on YOUR field data once it exists.

Joins confirmed diagnoses (console captures with a true label, or agronomist-confirmed
records) with the BME680 + soil readings from the 48 h before each one, then fits a gradient-
boosting classifier that predicts disease from conditions alone. Until enough labelled field
data exists, the console uses the published forecasting rules in raspberry_pi/env_risk.py.

    python3 ml/v2/train_env.py --sensors captures/sensors.jsonl --labels captures/detections.jsonl
"""
import argparse
import json
from collections import Counter

import numpy as np

MIN_PER_CLASS, MIN_TOTAL = 20, 120
FEATURES = ["t_mean", "t_min", "t_max", "rh_mean", "rh_min", "hours_rh90", "hours_rh85_18_27",
            "hours_warm_wet", "hours_hot_dry", "soil_mean", "soil_min", "pressure_trend"]


def features(readings, t_end):
    w = [r for r in readings if t_end - 48 * 3600 <= r["t"] <= t_end]
    if len(w) < 12:
        return None
    t = np.array([r.get("temp_c", np.nan) for r in w], float)
    rh = np.array([r.get("rh", np.nan) for r in w], float)
    soil = np.array([r.get("soil_moisture_pct", np.nan) for r in w], float)
    p = np.array([r.get("pressure_hpa", np.nan) for r in w], float)
    frac = len(w) / 48.0  # readings per hour, to turn counts into hours
    hrs = lambda m: float(np.nansum(m)) / max(frac, 1e-6)
    return [np.nanmean(t), np.nanmin(t), np.nanmax(t), np.nanmean(rh), np.nanmin(rh), hrs(rh >= 90),
            hrs((rh > 85) & (t >= 18) & (t <= 27)), hrs((rh >= 90) & (t >= 24) & (t <= 30)),
            hrs((t > 27) & (rh < 50)), np.nanmean(soil), np.nanmin(soil),
            (p[-1] - p[0]) if np.isfinite(p).sum() > 1 else 0.0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sensors", required=True)
    ap.add_argument("--labels", required=True)
    ap.add_argument("--out", default="env_model.json")
    args = ap.parse_args()
    readings = sorted((json.loads(l) for l in open(args.sensors) if l.strip()), key=lambda r: r["t"])
    labels = [json.loads(l) for l in open(args.labels) if l.strip()]
    labels = [r for r in labels if r.get("truth") and r["truth"] != "no_tomato_leaf"]

    X, y = [], []
    for r in labels:
        f = features(readings, r["t"])
        if f is not None:
            X.append(f)
            y.append(r["truth"])
    counts = Counter(y)
    usable = {k for k, n in counts.items() if n >= MIN_PER_CLASS}
    print(f"labelled records with 48 h of sensor history: {len(y)}  per class: {dict(counts)}")
    if len(y) < MIN_TOTAL or len(usable) < 2:
        print(f"Not enough real field data yet (need >= {MIN_TOTAL} records and >= {MIN_PER_CLASS} per class "
              "for at least 2 classes). The console keeps using the published forecasting rules.")
        return

    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.model_selection import cross_val_score
    keep = [i for i, c in enumerate(y) if c in usable]
    Xa, ya = np.array(X)[keep], np.array(y)[keep]
    clf = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=300, class_weight="balanced")
    scores = cross_val_score(clf, Xa, ya, cv=5, scoring="f1_macro")
    print(f"5-fold macro-F1: {scores.mean():.3f} +/- {scores.std():.3f}")
    clf.fit(Xa, ya)
    json.dump({"features": FEATURES, "classes": sorted(usable), "cv_macro_f1": float(scores.mean()),
               "n": int(len(ya))}, open(args.out, "w"), indent=2)
    import pickle
    pickle.dump(clf, open(args.out.replace(".json", ".pkl"), "wb"))
    print(f"saved {args.out}")


if __name__ == "__main__":
    main()
