"""Google Maps Platform: Routes API (route), Roads (snap GPS), Places (repair shops).

Cost control: fetch a route once per ride (not per GPS fix), snap traces in
batches of 100, and always send a Places field mask so only needed fields are billed.
"""
from __future__ import annotations

from ..curves.geometry import LatLon
from .http import client

ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
SNAP_URL = "https://roads.googleapis.com/v1/snapToRoads"
PLACES_TEXT_URL = "https://places.googleapis.com/v1/places:searchText"
PLACES_FIELDS = ",".join(
    [
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.location",
        "places.nationalPhoneNumber",
        "places.currentOpeningHours.openNow",
        "places.googleMapsUri",
    ]
)


class MapsError(RuntimeError):
    pass


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


def route(origin: LatLon, destination: LatLon, api_key: str, travel_mode: str = "TWO_WHEELER") -> list[LatLon]:
    """Full-detail route polyline from the Routes API (computeRoutes).

    The legacy Directions API can't be enabled on new Cloud projects (Oct 2026), so we
    use Routes API. TWO_WHEELER mode (supported in PH) plans motorcycle routes but
    bills at a higher rate; set ROUTE_TRAVEL_MODE=DRIVE to save cost (then highways
    are avoided, since bikes under 400cc are barred from most PH expressways).
    """
    body = {
        "origin": {"location": {"latLng": {"latitude": origin[0], "longitude": origin[1]}}},
        "destination": {"location": {"latLng": {"latitude": destination[0], "longitude": destination[1]}}},
        "travelMode": travel_mode,
        "polylineQuality": "HIGH_QUALITY",  # the overview polyline is simplified and hides tight curves
        "computeAlternativeRoutes": False,
        "units": "METRIC",
    }
    if travel_mode == "DRIVE":
        body["routeModifiers"] = {"avoidHighways": True}
    headers = {"X-Goog-Api-Key": api_key, "X-Goog-FieldMask": "routes.polyline.encodedPolyline,routes.distanceMeters"}
    with client() as c:
        resp = c.post(ROUTES_URL, json=body, headers=headers)
    if resp.status_code != 200:
        raise MapsError(f"Routes API {resp.status_code}: {resp.text[:200]}")
    routes = resp.json().get("routes", [])
    if not routes:
        raise MapsError("Routes API returned no route")
    return decode_polyline(routes[0]["polyline"]["encodedPolyline"])


def snap_to_roads(points: list[LatLon], api_key: str) -> list[LatLon]:
    """Snap a noisy GPS trace to the road network (interpolated). Batches of 100 points."""
    out: list[LatLon] = []
    with client() as c:
        for i in range(0, len(points), 100):
            chunk = points[i : i + 100]
            path = "|".join(f"{lat},{lon}" for lat, lon in chunk)
            data = c.get(SNAP_URL, params={"path": path, "interpolate": "true", "key": api_key}).raise_for_status().json()
            for p in data.get("snappedPoints", []):
                loc = p["location"]
                pt = (loc["latitude"], loc["longitude"])
                if not out or out[-1] != pt:
                    out.append(pt)
    return out


def repair_shops(position: LatLon, api_key: str, *, radius_m: float = 3000, open_now: bool = True, limit: int = 5) -> list[dict]:
    """Nearby motorcycle repair shops, nearest-biased."""
    body = {
        "textQuery": "motorcycle repair shop",
        "openNow": open_now,
        "pageSize": limit,
        "locationBias": {"circle": {"center": {"latitude": position[0], "longitude": position[1]}, "radius": radius_m}},
    }
    headers = {"X-Goog-Api-Key": api_key, "X-Goog-FieldMask": PLACES_FIELDS}
    with client() as c:
        data = c.post(PLACES_TEXT_URL, json=body, headers=headers).raise_for_status().json()
    shops = []
    for p in data.get("places", []):
        loc = p.get("location", {})
        shops.append(
            {
                "id": p.get("id"),
                "name": p.get("displayName", {}).get("text"),
                "address": p.get("formattedAddress"),
                "lat": loc.get("latitude"),
                "lon": loc.get("longitude"),
                "phone": p.get("nationalPhoneNumber"),
                "open_now": p.get("currentOpeningHours", {}).get("openNow"),
                "maps_url": p.get("googleMapsUri"),
            }
        )
    return shops
