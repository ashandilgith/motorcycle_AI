"""Crash hotspots along a route.

Loads a GeoJSON FeatureCollection of Point features with properties
{"name": str, "source": str, "crashes": int?}. The bundled file is EMPTY on
purpose: fill it from MMDA / LGU road-crash data (e.g. MMDA MMARAS reports,
Quezon City open data) and, later, from riders' own near-miss reports.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from ..curves.geometry import LatLon, distance_along

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "hotspots.geojson"
NEAR_ROUTE_M = 40.0


@lru_cache(maxsize=4)
def load(path: str = str(DEFAULT_PATH)) -> tuple[dict, ...]:
    p = Path(path)
    if not p.exists():
        return ()
    fc = json.loads(p.read_text())
    out = []
    for f in fc.get("features", []):
        if f.get("geometry", {}).get("type") != "Point":
            continue
        lon, lat = f["geometry"]["coordinates"][:2]
        out.append({"lat": lat, "lon": lon, **f.get("properties", {})})
    return tuple(out)


def along_route(route: list[LatLon], cum: list[float], hotspots: tuple[dict, ...] | list[dict]) -> list[dict]:
    """Hotspots within NEAR_ROUTE_M of the route, with their distance along it, in order."""
    hits = []
    for h in hotspots:
        d_along, off = distance_along(route, cum, (h["lat"], h["lon"]))
        if off <= NEAR_ROUTE_M:
            hits.append({**h, "at_m": round(d_along, 1)})
    return sorted(hits, key=lambda h: h["at_m"])
