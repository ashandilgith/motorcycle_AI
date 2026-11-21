"""Per-bike engine-sound anomaly scoring.

Each bike gets its own baseline from its first few healthy recordings. A new
recording is scored by how far it sits from that baseline (mean absolute
z-score across feature dimensions). No labels needed to start; once mechanics
confirm faults, the stored clips become training data for a fault classifier.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

MIN_BASELINE_CLIPS = 3
# Starting threshold; tune from the false-alarm vs miss trade-off in the brief.
ANOMALY_THRESHOLD = 2.5
STD_FLOOR = 0.05


@dataclass
class SoundScore:
    score: float
    is_anomaly: bool
    baseline_clips: int
    top_bands: list[int]   # mel bands that moved most (low index = low pitch)
    note: str


def score(embedding: np.ndarray, baseline: list[np.ndarray]) -> SoundScore:
    if len(baseline) < MIN_BASELINE_CLIPS:
        return SoundScore(
            score=0.0,
            is_anomaly=False,
            baseline_clips=len(baseline),
            top_bands=[],
            note=f"Need {MIN_BASELINE_CLIPS - len(baseline)} more healthy recording(s) to build this bike's baseline.",
        )
    base = np.stack(baseline)
    mu = base.mean(axis=0)
    sd = np.maximum(base.std(axis=0), STD_FLOOR)
    z = np.abs((embedding - mu) / sd)
    s = float(z.mean())
    n_mels = len(embedding) // 2
    band_z = z[:n_mels]
    top = [int(i) for i in np.argsort(band_z)[::-1][:3]]
    anomalous = s > ANOMALY_THRESHOLD
    return SoundScore(
        score=round(s, 3),
        is_anomaly=anomalous,
        baseline_clips=len(baseline),
        top_bands=top,
        note=(
            "Sounds different from this bike's normal. Record again in the same spot to confirm, then have it checked."
            if anomalous
            else "Within this bike's normal range."
        ),
    )
