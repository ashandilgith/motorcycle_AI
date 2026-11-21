"""Start-of-ride bundle: everything the phone needs, fetched once.

Route source:
  - destination + Google key  -> Google Routes API (exact planned route)
  - otherwise                 -> OpenStreetMap road ahead from position + heading
Plus curves with safe speeds, wet flag from weather, and hotspots on the route.
Each external call degrades gracefully: a failure is reported in `warnings`
instead of failing the whole ride.
"""
from __future__ import annotations

from dataclasses import asdict

from .config import settings
from .curves.geometry import LatLon, find_curves
from .integrations import google_maps, hotspots, osm, weather


def start_ride(
    position: LatLon,
    destination: LatLon | None = None,
    heading_deg: float | None = None,
    lookahead_m: float = 1500,
) -> dict:
    warnings: list[str] = []
    route: list[LatLon] = []
    source = None

    if destination and settings.google_maps_api_key:
        try:
            route = google_maps.route(position, destination, settings.google_maps_api_key, settings.route_travel_mode)
            source = "google_routes"
        except Exception as exc:
            warnings.append(f"Google route failed ({exc}); falling back to OpenStreetMap")
    elif destination:
        warnings.append("GOOGLE_MAPS_API_KEY not set; using OpenStreetMap road ahead instead of a planned route")

    if not route:
        try:
            graph = osm.fetch_roads(position, radius_m=min(max(lookahead_m, 500), 1000))
            route = osm.road_ahead(graph, position, heading_deg, lookahead_m)
            source = "osm_road_ahead"
            if heading_deg is None:
                warnings.append("No heading given; direction along the road is a guess")
        except Exception as exc:
            warnings.append(f"OpenStreetMap fetch failed ({exc})")

    try:
        wx = asdict(weather.road_weather(position))
    except Exception as exc:
        wx = None
        warnings.append(f"Weather unavailable ({exc}); assuming dry, so ride carefully if it's raining")

    if len(route) < 3:
        return {"source": source, "points": route, "curves": [], "hotspots": [], "weather": wx, "warnings": warnings + ["No usable road geometry"]}

    pts, cum, curves = find_curves(route)
    return {
        "source": source,
        "points": pts,
        "length_m": round(cum[-1], 1),
        "curves": [asdict(c) for c in curves],
        "hotspots": hotspots.along_route(pts, cum, hotspots.load()),
        "weather": wx,
        "conditions": {
            "wet": bool(wx and wx["wet"]),
            "night": bool(wx and wx["is_day"] is False),
        },
        "warnings": warnings,
    }
