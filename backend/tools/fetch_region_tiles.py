"""Download the region road tiles named in backend/data/region_tiles.json into OSM_TILE_DIR.

Run at image build time (Dockerfile). The tiles are built monthly by the "Build region road data"
GitHub workflow and stored as a release file, so they don't bloat the code repository.
Fails (non-zero exit) if the download or checksum fails, so a broken build never goes live.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tarfile
import urllib.request
from pathlib import Path

MANIFEST = Path(__file__).resolve().parents[1] / "data" / "region_tiles.json"


def main() -> int:
    if not MANIFEST.exists():
        print("no region_tiles.json: skipping region road data")
        return 0
    m = json.loads(MANIFEST.read_text())
    out = Path(os.getenv("OSM_TILE_DIR", MANIFEST.parent / "osm_tiles"))
    out.mkdir(parents=True, exist_ok=True)
    tmp = out.parent / "region_tiles.tar"
    print(f"downloading {m['url']} ({m['bytes'] / 1e6:.1f} MB)", flush=True)
    for attempt in range(3):
        try:
            urllib.request.urlretrieve(m["url"], tmp)
            break
        except Exception as exc:  # retry network hiccups
            print(f"  attempt {attempt + 1} failed: {exc}", flush=True)
    else:
        return 1
    digest = hashlib.sha256(tmp.read_bytes()).hexdigest()
    if digest != m["sha256"]:
        print(f"checksum mismatch: got {digest}, expected {m['sha256']}")
        return 1
    with tarfile.open(tmp) as tar:
        tar.extractall(out, filter="data")
    tmp.unlink()
    print(f"region road data ready: {m['tiles']} tiles, built {m['built']}, area {m['bbox']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
