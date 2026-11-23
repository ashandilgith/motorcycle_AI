"""Fill the road-tile cache ahead of time, so rides never wait on the slow public Overpass servers.

Usage (from backend/):
  python -m tools.prewarm_osm --around 14.6240,121.2064 --km 3          # a test area
  python -m tools.prewarm_osm --gpx my_route.gpx --km 1                  # everything within 1 km of a route
  python -m tools.prewarm_osm --bbox 14.55,120.95,14.70,121.10            # a box (south,west,north,east)

Polite to the free servers: one request at a time, a pause between tiles, skips tiles already cached.
Tiles land in backend/data/osm_tiles/ (set OSM_TILE_DIR to change). Deploy that folder with the backend.
"""
from __future__ import annotations

import argparse
import math
import sys
import time
import xml.etree.ElementTree as ET

from app.integrations import osm


def tiles_for_bbox(s, w, n, e):
    i0, j0 = osm.tile_of(s, w)
    i1, j1 = osm.tile_of(n, e)
    return [(i, j) for i in range(i0, i1 + 1) for j in range(j0, j1 + 1)]


def tiles_for_gpx(path, km):
    ns = {"g": "http://www.topografix.com/GPX/1/1"}
    pts = [(float(p.get("lat")), float(p.get("lon"))) for p in ET.parse(path).getroot().iterfind(".//g:trkpt", ns)]
    tiles = set()
    for p in pts[:: max(1, len(pts) // 2000)]:
        tiles.update(osm.tiles_for_circle(p, km * 1000))
    return sorted(tiles)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--around", help="lat,lon")
    g.add_argument("--bbox", help="south,west,north,east")
    g.add_argument("--gpx")
    ap.add_argument("--km", type=float, default=2.0)
    ap.add_argument("--pause", type=float, default=2.0, help="seconds between requests")
    ap.add_argument("--force", action="store_true", help="re-download cached tiles")
    ap.add_argument("--per-tile", action="store_true", help="download tile by tile instead of one area request")
    a = ap.parse_args(argv)

    if a.around:
        lat, lon = map(float, a.around.split(","))
        tiles = osm.tiles_for_circle((lat, lon), a.km * 1000)
    elif a.bbox:
        tiles = tiles_for_bbox(*map(float, a.bbox.split(",")))
    else:
        tiles = tiles_for_gpx(a.gpx, a.km)

    todo = [t for t in tiles if a.force or osm.load_tile(t, max_age_s=osm.TILE_MAX_AGE_S) is None]
    print(f"{len(tiles)} tiles in area, {len(todo)} to download")
    if todo and not a.per_tile:
        # One request for the whole box, then slice into tiles (much faster on busy public servers).
        s_, w_ = min(t[0] for t in todo) * osm.TILE_DEG, min(t[1] for t in todo) * osm.TILE_DEG
        n_, e_ = (max(t[0] for t in todo) + 1) * osm.TILE_DEG, (max(t[1] for t in todo) + 1) * osm.TILE_DEG
        try:
            print(f"downloading area {s_:.2f},{w_:.2f},{n_:.2f},{e_:.2f} in one request…")
            pieces = osm.split_into_tiles(osm.fetch_bbox(s_, w_, n_, e_))
            for t in todo:
                osm.save_tile(t, pieces.get(t, []))  # empty tile = no roads there (fields, water)
            print(f"done: {len(todo)} tiles, {sum(len(v) for v in pieces.values())} elements")
            return 0
        except Exception as exc:
            print(f"area download failed ({exc}); falling back to tile by tile")
    failed = 0
    for k, t in enumerate(todo, 1):
        try:
            els = osm.fetch_tile(t)
            osm.save_tile(t, els)
            print(f"[{k}/{len(todo)}] tile {t}: {sum(e['type'] == 'way' for e in els)} roads")
        except Exception as exc:
            failed += 1
            print(f"[{k}/{len(todo)}] tile {t}: FAILED ({exc}); run again later to retry")
        time.sleep(a.pause)
    print(f"done, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
