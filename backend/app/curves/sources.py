"""Where road geometry comes from.

MVP: Google Directions API (when the rider sets a destination).
Next: cached OpenStreetMap ways for free riding with no destination (cheaper,
works offline). Never call a maps API once per GPS fix: fetch once per route,
send the curves to the phone.
"""
from __future__ import annotations

import httpx

from .geometry import LatLon

DIRECTIONS_URL = "https://maps.googleapis.com/maps/api/directions/json"


def decode_polyline(encoded: str) -> list[LatLon]:
    """Decode a Google encoded polyline into (lat, lon) pairs."""
    points: list[LatLon] = []
    index = lat = lon = 0
    while index < len(encoded):
        for is_lon in (False, True):
            shift = result = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if is_lon:
                lon += delta
            else:
                lat += delta
        points.append((lat / 1e5, lon / 1e5))
    return points


async def google_route(origin: LatLon, destination: LatLon, api_key: str) -> list[LatLon]:
    """Fetch a two-wheeler-friendly route and return its full-detail polyline.

    Uses each step's polyline (more detail than overview_polyline, which is
    simplified and would hide tight curves).
    """
    params = {
        "origin": f"{origin[0]},{origin[1]}",
        "destination": f"{destination[0]},{destination[1]}",
        "mode": "driving",
        "avoid": "highways",  # motorcycles under 400cc are barred from most PH expressways
        "key": api_key,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(DIRECTIONS_URL, params=params)
        resp.raise_for_status()
        data = resp.json()
    if data.get("status") != "OK":
        raise RuntimeError(f"Directions API: {data.get('status')} {data.get('error_message', '')}")
    points: list[LatLon] = []
    for leg in data["routes"][0]["legs"]:
        for step in leg["steps"]:
            pts = decode_polyline(step["polyline"]["points"])
            if points and pts and points[-1] == pts[0]:
                pts = pts[1:]
            points.extend(pts)
    return points


# TODO(phase 2): osm_roads_near(position, radius_m) via a cached Overpass extract of
# Metro Manila, so the alert works without a destination and without per-ride API cost.
