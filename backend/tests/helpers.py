"""Synthetic test data: a road with a known curve, and engine-like audio."""
from __future__ import annotations

import io
import math
import wave

import numpy as np

ORIGIN = (14.5995, 120.9842)  # Manila
M_PER_DEG_LAT = 111_195.0


def offset(origin, dx_m, dy_m):
    lat0, lon0 = origin
    return (
        lat0 + dy_m / M_PER_DEG_LAT,
        lon0 + dx_m / (M_PER_DEG_LAT * math.cos(math.radians(lat0))),
    )


def road_with_curve(radius_m=40.0, straight_m=300.0, turn_deg=90.0, step_m=5.0):
    """Straight north for `straight_m`, then a right-hand arc of `radius_m`, then straight east."""
    pts = [offset(ORIGIN, 0, y) for y in np.arange(0, straight_m + 0.1, step_m)]
    cx, cy = radius_m, straight_m  # arc centre to the right
    n = max(int(radius_m * math.radians(turn_deg) / step_m), 4)
    for i in range(1, n + 1):
        a = math.pi - math.radians(turn_deg) * i / n
        pts.append(offset(ORIGIN, cx + radius_m * math.cos(a), cy + radius_m * math.sin(a)))
    ex, ey = cx + radius_m * math.cos(math.pi - math.radians(turn_deg)), cy + radius_m * math.sin(math.pi - math.radians(turn_deg))
    for d in np.arange(step_m, 200.1, step_m):
        pts.append(offset(ORIGIN, ex + d, ey))
    return pts


def engine_wav(seconds=2.0, rpm_hz=30.0, knock=0.0, seed=0, rate=16_000) -> bytes:
    """Harmonic engine-like hum + noise; `knock` adds a high-frequency tick (a 'fault')."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * rate)) / rate
    sig = sum(np.sin(2 * np.pi * rpm_hz * k * t) / k for k in range(1, 8))
    sig = sig + 0.05 * rng.standard_normal(len(t))
    if knock:
        sig = sig + knock * np.sin(2 * np.pi * 3_500 * t) * (np.sin(2 * np.pi * 8 * t) > 0.9)
    sig = 0.3 * sig / np.max(np.abs(sig))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((sig * 32767).astype(np.int16).tobytes())
    return buf.getvalue()
