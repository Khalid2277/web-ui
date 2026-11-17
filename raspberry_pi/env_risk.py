"""Environmental disease-risk models for tomato, from BME680 + soil-moisture readings.

Each score is a published plant-pathology forecasting rule adapted to what the sensors can
measure. Leaf wetness is not measured directly, so (as is common) hours with RH >= 90 % are
used as the wetness proxy.

- Late blight:   Hutton criteria (Dancey et al. 2017, the UK successor of the Smith period):
                 2 consecutive days, each with min T >= 10 C and >= 6 h of RH >= 90 %.
- Early blight / Septoria: TOM-CAST disease-severity values (Pitblado 1992), accumulated
                 over 7 days; 15-20 DSV is the usual spray threshold.
- Leaf mould:    sustained RH > 85 % at 18-27 C (Passalora fulva optimum ~ 22-24 C).
- Bacterial spot: warm (24-30 C) and wet (RH >= 90 %) hours.
- Spider mites:  favoured by hot (> 27 C), dry (RH < 50 %) conditions.

Pressure is shown as context and gas resistance is logged only: neither has a validated
link to tomato disease, so neither changes any risk.

fuse() combines these risks with the camera model as a bounded, boost-only prior. The image
stays dominant: the largest possible boost is RISK_GAIN (x1.35), so a humid week can raise
suspicion of a visible disease but cannot invent one, and dry weather never hides one.
"""
import math
import time
from collections import defaultdict

RISK_GAIN = 0.30                 # max log-boost; exp(0.30) ~ 1.35x
WET_RH = 90.0

# TOM-CAST DSV: for a mean temperature band, the leaf-wetness hours needed for DSV 1..4.
TOMCAST = [
    ((13, 17), (7, 16, 21, 99)),
    ((18, 20), (4, 9, 16, 23)),
    ((21, 25), (3, 6, 13, 21)),
    ((26, 29), (4, 9, 14, 23)),
]


def _hourly(readings):
    """Bucket readings into hours: {hour_start: (mean T, mean RH)}."""
    acc = defaultdict(list)
    for r in readings:
        if r.get("temp_c") is not None and r.get("rh") is not None:
            acc[int(r["t"] // 3600) * 3600].append((r["temp_c"], r["rh"]))
    return {h: (sum(t for t, _ in v) / len(v), sum(rh for _, rh in v) / len(v)) for h, v in acc.items()}


def _days(hourly):
    days = defaultdict(list)
    for h, v in hourly.items():
        days[time.strftime("%Y-%m-%d", time.localtime(h))].append(v)
    return dict(sorted(days.items()))


def _dsv(day):
    wet = [t for t, rh in day if rh >= WET_RH]
    if not wet:
        return 0
    mean_t = sum(wet) / len(wet)
    for (lo, hi), thresholds in TOMCAST:
        if lo <= mean_t <= hi:
            return sum(len(wet) >= h for h in thresholds)
    return 0


def risks(readings, now=None):
    """Return per-disease risk in [0, 1] with a plain-language reason, plus a data summary."""
    now = now or time.time()
    recent = [r for r in readings if now - r["t"] <= 7 * 86400]
    hourly = _hourly(recent)
    hours = len(hourly)
    out = {"hours_of_data": hours, "diseases": {}}
    if hours < 6:
        return out
    last48 = {h: v for h, v in hourly.items() if now - h <= 48 * 3600}
    days = _days(hourly)
    day_list = list(days.values())

    # Late blight: Hutton criteria on the last two days with data.
    def hutton_day(day):
        return min(t for t, _ in day) >= 10 and sum(rh >= WET_RH for _, rh in day) >= 6
    met = [hutton_day(d) for d in day_list[-2:]]
    lb = 1.0 if len(met) == 2 and all(met) else 0.5 if any(met) else 0.0
    out["diseases"]["late_blight"] = (lb, "Hutton criteria met on 2 consecutive days" if lb == 1 else
                                      "Hutton criteria met on 1 of the last 2 days" if lb else "Hutton criteria not met")

    # Early blight + Septoria: TOM-CAST accumulated over the last 7 days.
    dsv = sum(_dsv(d) for d in day_list[-7:])
    tc = min(1.0, dsv / 15.0)
    for k in ("early_blight", "septoria_leaf_spot"):
        out["diseases"][k] = (tc, f"TOM-CAST {dsv} DSV over 7 days (spray threshold ~15)")

    if last48:
        n = len(last48)
        mould = sum(rh > 85 and 18 <= t <= 27 for t, rh in last48.values()) / n
        out["diseases"]["leaf_mold"] = (min(1.0, mould * 1.5), f"{mould:.0%} of the last 48 h humid (RH > 85 %) at 18-27 C")
        bact = sum(rh >= WET_RH and 24 <= t <= 30 for t, rh in last48.values()) / n
        out["diseases"]["bacterial_spot"] = (min(1.0, bact * 2.0), f"{bact:.0%} of the last 48 h warm and wet")
        mites = sum(t > 27 and rh < 50 for t, rh in last48.values()) / n
        out["diseases"]["spider_mites"] = (min(1.0, mites * 1.5), f"{mites:.0%} of the last 48 h hot and dry")

    latest = max(readings, key=lambda r: r["t"])
    out["latest"] = {k: latest.get(k) for k in ("t", "temp_c", "rh", "pressure_hpa", "gas_ohm", "soil_moisture_pct")}
    soil = latest.get("soil_moisture_pct")
    if soil is not None:
        out["soil_note"] = ("Dry soil: water stress can show as yellowing or deficiency-like symptoms" if soil < 20 else
                            "Waterlogged soil: poor nutrient uptake and root stress" if soil > 80 else "Soil moisture in normal range")
    return out


def fuse(image_top, env):
    """Re-weight the image model's class probabilities by environmental risk (bounded prior).

    image_top: list of {"label", "p"} covering the model's classes (probabilities sum to ~1).
    Returns (fused list sorted by p, explanation list).
    """
    diseases = env.get("diseases", {}) if env else {}
    if not diseases:
        return image_top, []
    weighted, notes = [], []
    for item in image_top:
        risk = diseases.get(item["label"], (None, ""))[0]
        # Boost-only: favourable weather may raise suspicion (x1.0 .. x1.35) but never hides a
        # disease the camera can see.
        w = math.exp(RISK_GAIN * max(0.0, 2 * risk - 1)) if risk is not None else 1.0
        weighted.append({**item, "p_image": item["p"], "p": item["p"] * w})
        if risk is not None and risk >= 0.5 and item["p"] >= 0.05:
            notes.append(f"{item['label']}: environment favours it ({diseases[item['label']][1]})")
    total = sum(x["p"] for x in weighted) or 1.0
    for x in weighted:
        x["p"] /= total
    return sorted(weighted, key=lambda x: -x["p"]), notes
