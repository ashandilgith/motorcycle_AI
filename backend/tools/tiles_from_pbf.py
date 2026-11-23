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


class Roads(osmium.SimpleHandler):
    def __init__(self, s, w, n, e):
        super().__init__()
        self.box = (s, w, n, e)
        self.ways: list[dict] = []
        self.nodes: dict[int, dict] = {}

    def way(self, w):
        hw = w.tags.get("highway")
        if not hw or not ROAD_RE.match(hw):
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


def build(pbf: str, s: float, w: float, n: float, e: float) -> dict[tuple[int, int], list[dict]]:
    h = Roads(s, w, n, e)
    h.apply_file(pbf, locations=True, idx="flex_mem")
    pieces = osm.split_into_tiles(list(h.nodes.values()) + h.ways)
    i0, j0 = osm.tile_of(s, w)
    i1, j1 = osm.tile_of(n - 1e-9, e - 1e-9)
    wanted = {(i, j) for i in range(i0, i1 + 1) for j in range(j0, j1 + 1)}
    return {t: pieces.get(t, []) for t in wanted}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pbf", required=True)
    ap.add_argument("--bbox", required=True, help="south,west,north,east")
    a = ap.parse_args(argv)
    s, w, n, e = map(float, a.bbox.split(","))
    tiles = build(a.pbf, s, w, n, e)
    for t, els in tiles.items():
        osm.save_tile(t, els)
    roads = sum(sum(x["type"] == "way" for x in els) for els in tiles.values())
    print(f"wrote {len(tiles)} tiles ({sum(1 for v in tiles.values() if v)} with roads, {roads} road pieces) to {osm.TILE_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
