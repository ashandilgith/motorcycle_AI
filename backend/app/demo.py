"""Synthetic demo road: a straight run into a sharp curve. Used by the phone's
demo mode (try the alerts without riding) and by the tests."""
from __future__ import annotations

import math

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
