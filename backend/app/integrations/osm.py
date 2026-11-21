"""OpenStreetMap road geometry for free riding (no destination set).

Fetch the roads around the rider once (Overpass API), then guess the road
ahead by following the straightest continuation from the rider's heading.
Re-fetch when the rider leaves the guessed path (off_route_m) or nears its end.

Production note: the public Overpass server is rate-limited. Before launch,
run our own Overpass or pre-extract Metro Manila roads (Geofabrik PBF) and
serve tiles from the backend.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from ..curves.geometry import LatLon, haversine_m, to_local_xy
from .http import client

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
ROAD_TYPES = (
    "trunk|primary|secondary|tertiary|unclassified|residential|living_street|service"
    "|trunk_link|primary_link|secondary_link|tertiary_link"
)


@dataclass
class RoadGraph:
    nodes: dict[int, LatLon]
    adj: dict[int, set[int]]
    oneway_edges: set[tuple[int, int]]  # allowed direction only, for oneway ways


def parse_overpass(data: dict) -> RoadGraph:
    nodes: dict[int, LatLon] = {}
    adj: dict[int, set[int]] = {}
    oneway: set[tuple[int, int]] = set()
    for el in data.get("elements", []):
        if el["type"] == "node":
            nodes[el["id"]] = (el["lat"], el["lon"])
    for el in data.get("elements", []):
        if el["type"] != "way":
            continue
        ids = [n for n in el.get("nodes", []) if n in nodes]
        is_oneway = el.get("tags", {}).get("oneway") in ("yes", "1", "true")
        for a, b in zip(ids, ids[1:]):
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set()).add(a)
            if is_oneway:
                oneway.add((a, b))
    return RoadGraph(nodes, adj, oneway)


def fetch_roads(position: LatLon, radius_m: float = 1500) -> RoadGraph:
    query = (
        f'[out:json][timeout:25];way(around:{radius_m:.0f},{position[0]},{position[1]})'
        f'[highway~"^({ROAD_TYPES})$"];(._;>;);out body;'
    )
    with client(timeout=30) as c:
        data = c.post(OVERPASS_URL, data={"data": query}).raise_for_status().json()
    return parse_overpass(data)


def _bearing(a: LatLon, b: LatLon) -> float:
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    dlon = math.radians(b[1] - a[1])
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def _turn(b1: float, b2: float) -> float:
    return abs((b2 - b1 + 180) % 360 - 180)


def _allowed(g: RoadGraph, a: int, b: int) -> bool:
    return not ((b, a) in g.oneway_edges and (a, b) not in g.oneway_edges)


def road_ahead(g: RoadGraph, position: LatLon, heading_deg: float | None, lookahead_m: float = 1500) -> list[LatLon]:
    """Polyline from the rider's position along the most likely road ahead."""
    if not g.adj:
        return []
    # 1. nearest edge to the rider
    best = None
    for a, nbrs in g.adj.items():
        for b in nbrs:
            if a > b:
                continue
            (x1, y1), (x2, y2) = to_local_xy([g.nodes[a], g.nodes[b]], origin=position)
            dx, dy = x2 - x1, y2 - y1
            seg2 = dx * dx + dy * dy
            t = 0.0 if seg2 == 0 else max(0.0, min(1.0, (-x1 * dx - y1 * dy) / seg2))
            off = math.hypot(x1 + t * dx, y1 + t * dy)
            if best is None or off < best[0]:
                best = (off, a, b, t)
    _, a, b, t = best
    # start ON the road, not at the raw GPS fix: an off-road start point adds a fake kink (fake curve)
    pa, pb = g.nodes[a], g.nodes[b]
    start = (pa[0] + t * (pb[0] - pa[0]), pa[1] + t * (pb[1] - pa[1]))
    # 2. direction of travel: the edge direction closest to the heading (and legal)
    candidates = [(a, b), (b, a)]
    candidates = [e for e in candidates if _allowed(g, *e)] or candidates
    if heading_deg is not None:
        candidates.sort(key=lambda e: _turn(heading_deg, _bearing(g.nodes[e[0]], g.nodes[e[1]])))
    prev, cur = candidates[0]
    path = [start, g.nodes[cur]] if haversine_m(start, g.nodes[cur]) > 0.5 else [g.nodes[cur]]
    length = haversine_m(start, g.nodes[cur])
    seen = {prev, cur}
    # 3. follow the straightest legal continuation
    while length < lookahead_m:
        incoming = _bearing(g.nodes[prev], g.nodes[cur])
        options = [n for n in g.adj.get(cur, ()) if n != prev and n not in seen and _allowed(g, cur, n)]
        if not options:
            break
        nxt = min(options, key=lambda n: _turn(incoming, _bearing(g.nodes[cur], g.nodes[n])))
        length += haversine_m(g.nodes[cur], g.nodes[nxt])
        path.append(g.nodes[nxt])
        seen.add(nxt)
        prev, cur = cur, nxt
    return path
