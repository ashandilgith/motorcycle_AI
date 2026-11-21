"""Maintenance schedule: what is due next, adjusted for how the bike is used.

IMPORTANT: the default intervals below are PLACEHOLDERS for the skeleton.
Replace them with figures from each model's owner's manual (Honda/Yamaha PH)
before showing them to riders.
"""
from __future__ import annotations

# item -> interval in km (placeholder values, verify per model)
DEFAULT_INTERVALS_KM: dict[str, float] = {
    "engine_oil": 2_000,
    "gear_oil": 6_000,
    "cvt_cleaning": 6_000,
    "air_filter": 12_000,
    "spark_plug": 8_000,
    "coolant": 24_000,
    "brake_pads_check": 4_000,
}

# Heavy stop-and-go traffic, flood exposure and gig mileage are "severe service".
USAGE_FACTOR = {"commuter": 0.9, "gig": 0.75, "diy": 0.9}


def next_due(odometer_km: float, usage: str, service_log: list[dict]) -> list[dict]:
    """For each item: km since last done, km until due, and status."""
    factor = USAGE_FACTOR.get(usage, 0.9)
    last_done: dict[str, float] = {}
    for entry in service_log:
        last_done[entry["item"]] = max(last_done.get(entry["item"], 0.0), entry["odometer_km"])
    out = []
    for item, interval in DEFAULT_INTERVALS_KM.items():
        eff = interval * factor
        since = odometer_km - last_done.get(item, 0.0)
        remaining = eff - since
        status = "overdue" if remaining < 0 else "due_soon" if remaining < 0.15 * eff else "ok"
        out.append(
            {
                "item": item,
                "interval_km": round(eff),
                "km_since_last": round(since),
                "km_remaining": round(remaining),
                "status": status,
                "never_logged": item not in last_done,
            }
        )
    order = {"overdue": 0, "due_soon": 1, "ok": 2}
    return sorted(out, key=lambda r: (order[r["status"]], r["km_remaining"]))
