"""Road geometry: turn a road polyline into curves with a radius and a safe speed.

Pure functions, no I/O, so the same logic can be ported 1:1 to the phone
(Kotlin/Swift/Dart) for the real-time loop.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

EARTH_RADIUS_M = 6_371_000.0

LatLon = tuple[float, float]


def haversine_m(a: LatLon, b: LatLon) -> float:
    """Great-circle distance in metres."""
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def to_local_xy(points: list[LatLon], origin: LatLon | None = None) -> list[tuple[float, float]]:
    """Equirectangular projection to metres around `origin`. Accurate to well under 1%
    over a few km, which is all a curve look-ahead needs."""
    if not points:
        return []
    lat0, lon0 = origin or points[0]
    k = math.cos(math.radians(lat0))
    return [
        (
            math.radians(lon - lon0) * EARTH_RADIUS_M * k,
            math.radians(lat - lat0) * EARTH_RADIUS_M,
        )
        for lat, lon in points
    ]


def resample(points: list[LatLon], step_m: float = 10.0) -> list[LatLon]:
    """Resample a polyline to roughly even spacing so curvature is comparable along the road."""
    if len(points) < 2:
        return list(points)
    out = [points[0]]
    carry = 0.0
    for a, b in zip(points, points[1:]):
        seg = haversine_m(a, b)
        if seg == 0:
            continue
        d = step_m - carry
        while d <= seg:
            t = d / seg
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
            d += step_m
        carry = seg - (d - step_m)
    if out[-1] != points[-1]:
        out.append(points[-1])
    return out


def circumradius(p1: tuple[float, float], p2: tuple[float, float], p3: tuple[float, float]) -> float:
    """Radius of the circle through three points (metres). Straight line -> inf."""
    a = math.dist(p2, p3)
    b = math.dist(p1, p3)
    c = math.dist(p1, p2)
    cross = abs((p2[0] - p1[0]) * (p3[1] - p1[1]) - (p2[1] - p1[1]) * (p3[0] - p1[0]))
    if cross < 1e-9:
        return math.inf
    return (a * b * c) / (2 * cross)


def safe_speed_kmh(radius_m: float, lateral_accel_ms2: float) -> float:
    """v = sqrt(a_lat * r). a_lat ~2.5 m/s^2 dry, ~1.5 wet (starting guesses, tune with riders)."""
    if math.isinf(radius_m):
        return math.inf
    return math.sqrt(lateral_accel_ms2 * radius_m) * 3.6


@dataclass
class Curve:
    start_m: float          # distance along the route where the curve begins
    apex_m: float           # distance along the route of the tightest point
    end_m: float
    min_radius_m: float
    apex: LatLon
    safe_kmh_dry: float
    safe_kmh_wet: float


def find_curves(
    points: list[LatLon],
    *,
    step_m: float = 10.0,
    span: int = 2,
    max_radius_m: float = 200.0,
    min_length_m: float = 15.0,
    a_lat_dry: float = 2.5,
    a_lat_wet: float = 1.5,
) -> tuple[list[LatLon], list[float], list[Curve]]:
    """Find curves tighter than `max_radius_m`.

    Returns (resampled points, cumulative distance of each point, curves).
    `span` = how many resampled points either side to use for each radius estimate;
    a wider span smooths GPS/map noise.
    """
    pts = resample(points, step_m)
    xy = to_local_xy(pts)
    cum = [0.0]
    for a, b in zip(pts, pts[1:]):
        cum.append(cum[-1] + haversine_m(a, b))

    radii = [math.inf] * len(pts)
    for i in range(span, len(pts) - span):
        radii[i] = circumradius(xy[i - span], xy[i], xy[i + span])

    curves: list[Curve] = []
    i = 0
    while i < len(pts):
        if radii[i] < max_radius_m:
            j = i
            while j + 1 < len(pts) and radii[j + 1] < max_radius_m:
                j += 1
            if cum[j] - cum[i] >= min_length_m or i == j:
                k = min(range(i, j + 1), key=lambda n: radii[n])
                r = radii[k]
                curves.append(
                    Curve(
                        start_m=cum[i],
                        apex_m=cum[k],
                        end_m=cum[j],
                        min_radius_m=r,
                        apex=pts[k],
                        safe_kmh_dry=safe_speed_kmh(r, a_lat_dry),
                        safe_kmh_wet=safe_speed_kmh(r, a_lat_wet),
                    )
                )
            i = j + 1
        else:
            i += 1
    return pts, cum, curves


def distance_along(route: list[LatLon], cum: list[float], position: LatLon) -> tuple[float, float]:
    """Project `position` onto the route. Returns (distance along route m, off-route distance m)."""
    xy = to_local_xy(route, origin=position)
    best = (0.0, math.inf)
    for i in range(len(xy) - 1):
        (x1, y1), (x2, y2) = xy[i], xy[i + 1]
        dx, dy = x2 - x1, y2 - y1
        seg2 = dx * dx + dy * dy
        t = 0.0 if seg2 == 0 else max(0.0, min(1.0, (-x1 * dx - y1 * dy) / seg2))
        px, py = x1 + t * dx, y1 + t * dy
        off = math.hypot(px, py)
        if off < best[1]:
            best = (cum[i] + t * (cum[i + 1] - cum[i]), off)
    return best
