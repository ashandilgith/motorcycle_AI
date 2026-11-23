"""Road data without Overpass from the server: compact tiles, region build, phone-sent roads."""
import gzip
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.integrations import http, osm
from app.main import create_app

from .helpers import ORIGIN, offset
from .test_integrations import handler, overpass_payload


@pytest.fixture(autouse=True)
def no_live_overpass(tmp_path, monkeypatch):
    """Like Render: empty tile folder, no live Overpass, weather mocked."""
    monkeypatch.setattr(osm, "TILE_DIR", tmp_path / "tiles")
    monkeypatch.setattr(osm, "OVERPASS_LIVE", False)
    calls = []

    def h(req):
        calls.append(req.url.host)
        return handler(req)

    http.set_transport(httpx.MockTransport(h))
    yield calls
    http.set_transport(None)


def graph_edges(g):
    return {(a, b) for a, ns in g.adj.items() for b in ns}


def test_compact_tile_round_trip_keeps_the_road_graph():
    els = overpass_payload()["elements"]
    els[-1]["tags"]["oneway"] = "yes"
    t = osm.tile_of(*ORIGIN)
    osm.save_tile(t, els)
    back = osm.load_tile(t)
    a, b = osm.parse_overpass({"elements": els}), osm.parse_overpass({"elements": back})
    assert graph_edges(a) == graph_edges(b)
    assert len(a.oneway_edges) == len(b.oneway_edges) > 0
    # coordinates within 1e-6 degrees (~0.1 m)
    assert max(abs(x - y) for n in a.nodes for x, y in zip(a.nodes[n], b.nodes[n])) < 1e-6


def test_old_tile_format_still_loads():
    t = osm.tile_of(*ORIGIN)
    osm.TILE_DIR.mkdir(parents=True)
    with gzip.open(osm.TILE_DIR / f"{t[0]}_{t[1]}.json.gz", "wt") as fh:
        json.dump(overpass_payload(), fh)
    assert len(osm.load_tile(t)) == len(overpass_payload()["elements"])


def test_old_tiles_are_served_but_prewarm_can_refresh_them(monkeypatch):
    t = osm.tile_of(*ORIGIN)
    osm.save_tile(t, overpass_payload()["elements"])
    import os
    old = osm._tile_path(t).stat().st_mtime - 400 * 86400
    os.utime(osm._tile_path(t), (old, old))
    assert osm.load_tile(t) is not None
    assert osm.load_tile(t, max_age_s=osm.TILE_MAX_AGE_S) is None


def test_no_tiles_answers_fast_with_need_roads(no_live_overpass):
    c = TestClient(create_app(db_path=":memory:"))
    r = c.post("/ride/start", json={"position": offset(ORIGIN, 2, 5), "heading_deg": 0}).json()
    assert r["need_roads"] is True and r["points"] == [] and r["weather"]
    assert "overpass-api.de" not in no_live_overpass


def test_phone_sent_roads_give_curves(no_live_overpass):
    c = TestClient(create_app(db_path=":memory:"))
    body = {"position": offset(ORIGIN, 2, 5), "heading_deg": 0, "roads": overpass_payload()["elements"]}
    r = c.post("/ride/start", json=body).json()
    assert r["source"] == "osm_road_ahead" and len(r["curves"]) >= 1 and not r.get("need_roads")
    assert "overpass-api.de" not in no_live_overpass


def test_server_tiles_win_over_phone_roads():
    for t in osm.tiles_for_circle(ORIGIN, 1000):
        osm.save_tile(t, overpass_payload()["elements"])
    c = TestClient(create_app(db_path=":memory:"))
    junk = [{"type": "node", "id": 1, "lat": 0, "lon": 0}]
    r = c.post("/ride/start", json={"position": offset(ORIGIN, 2, 5), "heading_deg": 0, "roads": junk}).json()
    assert len(r["curves"]) >= 1


def test_phone_roads_far_away_or_junk_do_not_crash():
    c = TestClient(create_app(db_path=":memory:"))
    junk = [{"type": "node"}, {"type": "way", "nodes": ["x", None]}, "nope", {"type": "node", "id": 5, "lat": 999, "lon": 1}]
    r = c.post("/ride/start", json={"position": ORIGIN, "heading_deg": 0, "roads": junk})
    assert r.status_code in (200, 422)
    if r.status_code == 200:
        assert r.json()["curves"] == [] and not r.json().get("need_roads")


def test_huge_body_is_refused():
    c = TestClient(create_app(db_path=":memory:"))
    big = '{"position":[14.6,121.0],"roads":[' + ",".join(['{"type":"node","id":1,"lat":14.6,"lon":121.0}'] * 120_000) + "]}"
    r = c.post("/ride/start", content=big, headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_region_build_in_strips_matches_single_pass(tmp_path, monkeypatch):
    pytest.importorskip("osmium")
    from tools import tiles_from_pbf
    els = overpass_payload()["elements"]
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<osm version="0.6">']
    lines += [f'<node id="{e["id"]}" version="1" lat="{e["lat"]}" lon="{e["lon"]}"/>' for e in els if e["type"] == "node"]
    for e in els:
        if e["type"] == "way":
            lines.append(f'<way id="{e["id"]}" version="1">' + "".join(f'<nd ref="{n}"/>' for n in e["nodes"])
                         + f'<tag k="highway" v="{e["tags"]["highway"]}"/></way>')
    lines += ['<way id="98" version="1"><nd ref="1"/><nd ref="2"/><tag k="highway" v="service"/></way>', "</osm>"]
    f = tmp_path / "area.osm"
    f.write_text("\n".join(lines))
    bbox = f"{ORIGIN[0] - 0.02},{ORIGIN[1] - 0.02},{ORIGIN[0] + 0.02},{ORIGIN[1] + 0.02}"
    single = tiles_from_pbf.build(str(f), *map(float, bbox.split(",")), skip_service=True)
    monkeypatch.setattr(tiles_from_pbf, "BAND_TILES", 1)  # many strips
    assert tiles_from_pbf.main(["--pbf", str(f), "--bbox", bbox, "--skip-service"]) == 0
    for t, want in single.items():
        got = osm.load_tile(t)
        assert graph_edges(osm.parse_overpass({"elements": got})) == graph_edges(osm.parse_overpass({"elements": want}))
    assert all(x.get("tags", {}).get("highway") != "service" for v in single.values() for x in v)


def test_phone_roads_module_fetches_once_and_server_accepts_it():
    """web/js/roads.js with a fake Overpass: one download, reused nearby, trimmed, and usable by the server."""
    import shutil
    import subprocess
    from pathlib import Path
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js not installed")
    roads_js = (Path(__file__).resolve().parents[2] / "web" / "js" / "roads.js").as_uri()
    pos, near, far = offset(ORIGIN, 2, 5), offset(ORIGIN, 2, 200), offset(ORIGIN, 2, 900)
    script = f"""
const m = await import({json.dumps(roads_js)});
const payload = JSON.parse(process.argv[1]);
let calls = 0, firstFails = true;
globalThis.fetch = async (url) => {{
  calls++;
  if (firstFails && url.includes("overpass-api.de")) {{ firstFails = false; return {{ ok: false, status: 504 }}; }}
  return {{ ok: true, json: async () => payload }};
}};
const a = await m.roadsNear({json.dumps(list(pos))});
const callsAfterFirst = calls;
const b = await m.roadsNear({json.dumps(list(near))});   // inside the reuse zone: no download
const cached = m.cachedRoadsNear({json.dumps(list(far))});  // too far: nothing cached for there
await m.roadsNear({json.dumps(list(far))});
console.log(JSON.stringify({{ a, callsAfterFirst, cached, total: calls, bLen: b.length }}));
"""
    res = subprocess.run([node, "--input-type=module", "-e", script, json.dumps(overpass_payload())], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["callsAfterFirst"] == 2          # first server failed, mirror answered
    assert out["cached"] is None and out["total"] == 3  # far point needed one more download
    c = TestClient(create_app(db_path=":memory:"))
    r = c.post("/ride/start", json={"position": pos, "heading_deg": 0, "roads": out["a"]}).json()
    assert len(r["curves"]) >= 1
