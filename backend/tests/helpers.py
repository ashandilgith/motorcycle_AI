"""Synthetic test data: a road with a known curve, and engine-like audio."""
from __future__ import annotations

import io
import wave

import numpy as np

from app.demo import M_PER_DEG_LAT, ORIGIN, offset, road_with_curve  # noqa: F401


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
