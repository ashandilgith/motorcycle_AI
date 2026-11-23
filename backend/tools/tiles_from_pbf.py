"""Build road tiles from an OpenStreetMap extract file (.osm.pbf), no Overpass needed.

The reliable way to get road data: download the Philippines extract from Geofabrik
(https://download.geofabrik.de/asia/philippines.html, one file, updated daily), then:

  pip install osmium
  python -m tools.tiles_from_pbf --pbf philippines-latest.osm.pbf --bbox 14.59,121.17,14.66,121.24

Writes tiles to backend/data/osm_tiles/ (or OSM_TILE_DIR). Every tile in the box is written,
empty ones too (fields, water), so the server never has to go online for that area.
"""
from __future__ import annotations

import argparse
import re
import sys

import osmium

from app.integrations import osm

ROAD_RE = re.compile(rf"^({osm.ROAD_TYPES})$")
# Region build: Metro Manila, Rizal, Cavite, Laguna, Bulacan, Batangas
REGIONS = {"luzon-south": "13.50,120.55,15.25,121.75"}
BAND_TILES = 25  # build in strips of 25 tile rows (~0.25 degrees) to keep memory bounded


class Roads(osmium.SimpleHandler):
    def __init__(self, s, w, n, e, skip_service=False):
        super().__init__()
        self.box = (s, w, n, e)
        self.skip_service = skip_service
        self.ways: list[dict] = []
        self.nodes: dict[int, dict] = {}

    def way(self, w):
        hw = w.tags.get("highway")
        if not hw or not ROAD_RE.match(hw) or (self.skip_service and hw == "service"):
            return
        s, wl, n, e = self.box
        pts = []
        for nd in w.nodes:
            if not nd.location.valid():
                continue
            pts.append((nd.ref, nd.location.lat, nd.location.lon))
        if not any(s <= lat <= n and wl <= lon <= e for _, lat, lon in pts):
            return  # road doesn't enter the box
        tags = {"highway": hw}
        if w.tags.get("oneway"):
            tags["oneway"] = w.tags.get("oneway")
        self.ways.append({"type": "way", "id": w.id, "nodes": [p[0] for p in pts], "tags": tags})
        for ref, lat, lon in pts:
            self.nodes[ref] = {"type": "node", "id": ref, "lat": round(lat, 7), "lon": round(lon, 7)}


def build(pbf: str, s: float, w: float, n: float, e: float, skip_service: bool = False) -> dict[tuple[int, int], list[dict]]:
    """All tiles in the box (small boxes; returns everything in memory)."""
    h = Roads(s, w, n, e, skip_service)
    h.apply_file(pbf, locations=True, idx="flex_mem")
    pieces = osm.split_into_tiles(list(h.nodes.values()) + h.ways)
    i0, j0 = osm.tile_of(s, w)
    i1, j1 = osm.tile_of(n - 1e-9, e - 1e-9)
    wanted = {(i, j) for i in range(i0, i1 + 1) for j in range(j0, j1 + 1)}
    return {t: pieces.get(t, []) for t in wanted}


def build_banded(pbf: str, s: float, w: float, n: float, e: float, skip_service: bool = False) -> tuple[int, int, int]:
    """Big areas: one strip of tile rows at a time, saving as we go. Returns (tiles, with roads, road pieces)."""
    i0, j0 = osm.tile_of(s, w)
    i1, j1 = osm.tile_of(n - 1e-9, e - 1e-9)
    total = with_roads = pieces = 0
    for b0 in range(i0, i1 + 1, BAND_TILES):
        b1 = min(b0 + BAND_TILES - 1, i1)
        bs, bn = b0 * osm.TILE_DEG, (b1 + 1) * osm.TILE_DEG
        tiles = build(pbf, max(s, bs), w, min(n, bn), e, skip_service)
        for t, els in tiles.items():
            osm.save_tile(t, els)
        total += len(tiles)
        with_roads += sum(1 for v in tiles.values() if v)
        pieces += sum(sum(x["type"] == "way" for x in els) for els in tiles.values())
        print(f"  rows {b0}-{b1}: {len(tiles)} tiles", flush=True)
    return total, with_roads, pieces


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pbf", required=True)
    ap.add_argument("--bbox", help="south,west,north,east")
    ap.add_argument("--region", choices=sorted(REGIONS), help="named region instead of --bbox")
    ap.add_argument("--skip-service", action="store_true", help="leave out service roads (driveways, parking aisles)")
    a = ap.parse_args(argv)
    bbox = REGIONS[a.region] if a.region else a.bbox
    if not bbox:
        ap.error("give --bbox or --region")
    s, w, n, e = map(float, bbox.split(","))
    total, with_roads, roads = build_banded(a.pbf, s, w, n, e, a.skip_service)
    print(f"wrote {total} tiles ({with_roads} with roads, {roads} road pieces) to {osm.TILE_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
