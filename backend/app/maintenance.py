"""Maintenance schedule: what is due next, adjusted for how the bike is used.

Each item says where its interval comes from: "manual" (a manufacturer's owner's manual, cited
below) or "estimate" (a generic figure, shown to riders as an estimate). Add models to
MODEL_INTERVALS only with a cited source.
"""
from __future__ import annotations

import re

# Generic estimates for any scooter (not from a manual). Shown to riders as estimates.
DEFAULT_INTERVALS_KM: dict[str, float] = {
    "engine_oil": 2_000,
    "gear_oil": 6_000,
    "cvt_cleaning": 6_000,
    "air_filter": 12_000,
    "spark_plug": 8_000,
    "coolant": 24_000,
    "brake_pads_check": 4_000,
}

# Manufacturer intervals, matched by model name. Only items clearly legible in the source are listed;
# everything else falls back to the estimates above.
MODEL_INTERVALS: dict[str, dict] = {
    "nmax": {
        "match": r"\bn-?max\b",
        "source": "Yamaha NMAX 155 owner's manual (BBM1, Yamaha Motor Malaysia), periodic maintenance chart",
        "url": "https://www.yamaha-motor.com.my/wp-content/uploads/2020/10/BBM1-NMAX-155-EN.pdf",
        "intervals_km": {
            "engine_oil": 4_000,   # oil-change indicator: 1,000 km, then +3,000 km, then every 4,000 km
            "coolant": 12_000,
            "v_belt": 25_000,      # replace at 25,000 km even without wear
        },
    },
}

# Heavy stop-and-go traffic, flood exposure and gig mileage are "severe service".
USAGE_FACTOR = {"commuter": 0.9, "gig": 0.75, "diy": 0.9}


def model_profile(model: str | None) -> dict | None:
    for prof in MODEL_INTERVALS.values():
        if model and re.search(prof["match"], model, re.I):
            return prof
    return None


def next_due(odometer_km: float, usage: str, service_log: list[dict], model: str | None = None) -> list[dict]:
    """For each item: km since last done, km until due, status, and where the interval comes from."""
    factor = USAGE_FACTOR.get(usage, 0.9)
    prof = model_profile(model)
    manual = prof["intervals_km"] if prof else {}
    intervals = {**DEFAULT_INTERVALS_KM, **manual}
    last_done: dict[str, float] = {}
    for entry in service_log:
        last_done[entry["item"]] = max(last_done.get(entry["item"], 0.0), entry["odometer_km"])
    out = []
    for item, interval in intervals.items():
        eff = interval * factor
        since = odometer_km - last_done.get(item, 0.0)
        remaining = eff - since
        if item not in last_done and odometer_km > eff:
            status = "unknown"  # never logged and the bike is past one interval: we can't tell, so don't cry wolf
        else:
            status = "overdue" if remaining < 0 else "due_soon" if remaining < 0.15 * eff else "ok"
        out.append(
            {
                "item": item,
                "interval_km": round(eff),
                "km_since_last": round(since),
                "km_remaining": round(remaining),
                "status": status,
                "never_logged": item not in last_done,
                "source": "manual" if item in manual else "estimate",
                "source_name": prof["source"] if item in manual else None,
            }
        )
    order = {"overdue": 0, "due_soon": 1, "ok": 2, "unknown": 3}
    return sorted(out, key=lambda r: (order[r["status"]], r["km_remaining"]))
