"""OpenStreetMap road geometry for free riding (no destination set).

Fetch the roads around the rider once (Overpass API), then guess the road
ahead by following the straightest continuation from the rider's heading.
Re-fetch when the rider leaves the guessed path (off_route_m) or nears its end.

Road data comes from a tile cache on the server first (filled ahead of time by
`tools/prewarm_osm.py`), and only falls back to a live Overpass query for areas
not cached yet. The public Overpass servers are too slow to rely on live.
"""
from __future__ import annotations

import gzip
import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path

from ..curves.geometry import LatLon, haversine_m, to_local_xy
from .http import client

# Public servers are often overloaded (field check 21 Nov 2026: main server 502, mirrors >40 s for a
# 1.5 km query in Sampaloc; 600 m took 31 s). Try them in order with short timeouts; self-host before launch.
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
OVERPASS_TIMEOUT_S = 12
# Tile cache: 0.01 degree squares (~1.1 km) of road data, gzipped JSON on disk.
TILE_DEG = 0.01
TILE_DIR = Path(os.getenv("OSM_TILE_DIR", Path(__file__).resolve().parents[2] / "data" / "osm_tiles"))
TILE_MAX_AGE_S = 60 * 24 * 3600  # prewarm script refreshes tiles older than this; old tiles are still served
# Live Overpass from the server. Off on Render: the public servers don't answer its IPs, so the
# phone fetches the roads itself instead (see ride.start_ride and web/js/roads.js).
OVERPASS_LIVE = os.getenv("OVERPASS_LIVE", "1") not in ("0", "false", "no")
ROAD_TYPES = (
    "trunk|primary|secondary|tertiary|unclassified|residential|living_street|service"
    "|trunk_link|primary_link|secondary_link|tertiary_link"
)


class NoRoadData(RuntimeError):
    """Server has no roads here and won't ask Overpass: the phone should send them."""


@dataclass
class RoadGraph:
    nodes: dict[int, LatLon]
    adj: dict[int, set[int]]
    oneway_edges: set[tuple[int, int]]  # allowed direction only, for oneway ways


def parse_overpass(data: dict) -> RoadGraph:
    """Road graph from Overpass-style elements. Tolerates junk (elements can come from a phone)."""
    nodes: dict[int, LatLon] = {}
    adj: dict[int, set[int]] = {}
    oneway: set[tuple[int, int]] = set()
    elements = data.get("elements", [])
    for el in elements:
        try:
            if el["type"] == "node":
                lat, lon = float(el["lat"]), float(el["lon"])
                if -90 <= lat <= 90 and -180 <= lon <= 180:
                    nodes[int(el["id"])] = (lat, lon)
        except (KeyError, TypeError, ValueError):
            continue
    for el in elements:
        if not isinstance(el, dict) or el.get("type") != "way":
            continue
        try:
            ids = [int(n) for n in el.get("nodes", []) if int(n) in nodes]
        except (TypeError, ValueError):
            continue
        tags = el.get("tags") or {}
        is_oneway = isinstance(tags, dict) and tags.get("oneway") in ("yes", "1", "true")
        for a, b in zip(ids, ids[1:]):
            if a == b:
                continue
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set()).add(a)
            if is_oneway:
                oneway.add((a, b))
    return RoadGraph(nodes, adj, oneway)


# ---------- tile cache ----------
def tile_of(lat: float, lon: float) -> tuple[int, int]:
    return math.floor(lat / TILE_DEG), math.floor(lon / TILE_DEG)


def tiles_for_circle(position: LatLon, radius_m: float) -> list[tuple[int, int]]:
    dlat = radius_m / 111_195
    dlon = radius_m / (111_195 * math.cos(math.radians(position[0])))
    i0, j0 = tile_of(position[0] - dlat, position[1] - dlon)
    i1, j1 = tile_of(position[0] + dlat, position[1] + dlon)
    return [(i, j) for i in range(i0, i1 + 1) for j in range(j0, j1 + 1)]


def _tile_path(t: tuple[int, int]) -> Path:
    return TILE_DIR / f"{t[0]}_{t[1]}.json.gz"


def load_tile(t: tuple[int, int], max_age_s: float | None = None) -> list[dict] | None:
    """Elements of a cached tile, or None if we have no tile. Old tiles are still served
    (old road data beats none); pass max_age_s to treat old tiles as missing (prewarm script)."""
    path = _tile_path(t)
    if not path.exists():
        return None
    if max_age_s is not None and time.time() - path.stat().st_mtime > max_age_s:
        return None
    with gzip.open(path, "rt") as fh:
        data = json.load(fh)
    return unpack_tile(data) if data.get("v") == 2 else data["elements"]


def pack_tile(elements: list[dict]) -> dict:
    """Compact tile format (v2), ~4x smaller than Overpass JSON:
    nodes sorted by id, stored as delta-coded ids and 1e-6 degree coordinates;
    ways as [highway, oneway, node indexes]. Way ids are not needed."""
    nodes = sorted((e for e in elements if e["type"] == "node"), key=lambda e: e["id"])
    index = {n["id"]: i for i, n in enumerate(nodes)}
    ids, lats, lons = [], [], []
    pid = plat = plon = 0
    for n in nodes:
        lat, lon = round(n["lat"] * 1e6), round(n["lon"] * 1e6)
        ids.append(n["id"] - pid); lats.append(lat - plat); lons.append(lon - plon)
        pid, plat, plon = n["id"], lat, lon
    ways = []
    for w in (e for e in elements if e["type"] == "way"):
        refs = [index[i] for i in w.get("nodes", []) if i in index]
        if len(refs) >= 2:
            tags = w.get("tags", {})
            ways.append([tags.get("highway", ""), tags.get("oneway", ""), refs])
    return {"v": 2, "ids": ids, "lat": lats, "lon": lons, "ways": ways}


def unpack_tile(d: dict) -> list[dict]:
    els: list[dict] = []
    ids: list[int] = []
    i = la = lo = 0
    for di, dla, dlo in zip(d["ids"], d["lat"], d["lon"]):
        i, la, lo = i + di, la + dla, lo + dlo
        ids.append(i)
        els.append({"type": "node", "id": i, "lat": la / 1e6, "lon": lo / 1e6})
    for k, (hw, oneway, refs) in enumerate(d["ways"]):
        tags = {"highway": hw}
        if oneway:
            tags["oneway"] = oneway
        # synthetic id, unique per way shape so merging tiles keeps one copy of a shared way
        els.append({"type": "way", "id": hash((ids[refs[0]], ids[refs[-1]], len(refs))), "nodes": [ids[r] for r in refs], "tags": tags})
    return els


def save_tile(t: tuple[int, int], elements: list[dict]) -> None:
    TILE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _tile_path(t).with_suffix(".tmp")
    with gzip.open(tmp, "wt", compresslevel=9) as fh:
        json.dump(pack_tile(elements), fh, separators=(",", ":"))
    tmp.replace(_tile_path(t))


def fetch_tile(t: tuple[int, int], timeout_s: int = 90) -> list[dict]:
    """Download one tile from Overpass (for the prewarm script: patient, not used mid-ride)."""
    s, w = t[0] * TILE_DEG, t[1] * TILE_DEG
    bbox = f"{s:.4f},{w:.4f},{s + TILE_DEG:.4f},{w + TILE_DEG:.4f}"
    query = f'[out:json][timeout:{timeout_s}];way({bbox})[highway~"^({ROAD_TYPES})$"];(._;>;);out body;'
    errors = []
    for url in OVERPASS_URLS:
        try:
            with client(timeout=timeout_s + 10) as c:
                return c.post(url, data={"data": query}).raise_for_status().json()["elements"]
        except Exception as exc:
            errors.append(f"{url.split('/')[2]}: {type(exc).__name__}")
    raise RuntimeError("tile download failed: " + "; ".join(errors))


def fetch_bbox(s: float, w: float, n: float, e: float, timeout_s: int = 180) -> list[dict]:
    """Download a whole area in ONE request (much faster than tile by tile on busy servers)."""
    query = f'[out:json][timeout:{timeout_s}];way({s:.4f},{w:.4f},{n:.4f},{e:.4f})[highway~"^({ROAD_TYPES})$"];(._;>;);out body;'
    errors = []
    for url in OVERPASS_URLS:
        try:
            with client(timeout=timeout_s + 20) as c:
                return c.post(url, data={"data": query}).raise_for_status().json()["elements"]
        except Exception as exc:
            errors.append(f"{url.split('/')[2]}: {type(exc).__name__}")
    raise RuntimeError("area download failed: " + "; ".join(errors))


def split_into_tiles(elements: list[dict]) -> dict[tuple[int, int], list[dict]]:
    """Cut an area download into cache tiles. A tile holds every road with a point inside it,
    plus all of that road's points (same as a per-tile download), so neighbouring tiles join up."""
    nodes = {e["id"]: e for e in elements if e["type"] == "node"}
    tiles: dict[tuple[int, int], dict[tuple[str, int], dict]] = {}
    for way in (e for e in elements if e["type"] == "way"):
        pts = [nodes[i] for i in way.get("nodes", []) if i in nodes]
        for t in {tile_of(n["lat"], n["lon"]) for n in pts}:
            bucket = tiles.setdefault(t, {})
            bucket[("way", way["id"])] = way
            for n in pts:
                bucket[("node", n["id"])] = n
    return {t: list(b.values()) for t, b in tiles.items()}


def cached_roads(position: LatLon, radius_m: float, allow_partial: bool = False) -> RoadGraph | None:
    """Road graph from cached tiles. Needs every tile the circle touches, unless allow_partial,
    in which case the rider's own tile is enough (near the edge of the cached area)."""
    merged: dict[tuple[str, int], dict] = {}
    for t in tiles_for_circle(position, radius_m):
        els = load_tile(t)
        if els is None:
            if allow_partial and t != tile_of(*position):
                continue
            return None
        for el in els:
            merged[(el["type"], el["id"])] = el
    return parse_overpass({"elements": list(merged.values())})


def fetch_roads(position: LatLon, radius_m: float = 800) -> RoadGraph:
    cached = cached_roads(position, radius_m)
    if cached is not None:
        return cached
    partial = cached_roads(position, radius_m, allow_partial=True)
    if partial is not None and partial.adj:
        return partial  # edge of the cached area: use what we have rather than wait on Overpass
    if not OVERPASS_LIVE:
        raise NoRoadData("no road data for this area on the server")
    query = (
        f'[out:json][timeout:{OVERPASS_TIMEOUT_S}];way(around:{radius_m:.0f},{position[0]},{position[1]})'
        f'[highway~"^({ROAD_TYPES})$"];(._;>;);out body;'
    )
    errors = []
    for url in OVERPASS_URLS:
        try:
            with client(timeout=OVERPASS_TIMEOUT_S + 3) as c:
                return parse_overpass(c.post(url, data={"data": query}).raise_for_status().json())
        except Exception as exc:  # try the next server
            errors.append(f"{url.split('/')[2]}: {type(exc).__name__}")
    raise RuntimeError("all Overpass servers failed: " + "; ".join(errors))


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
