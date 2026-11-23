"""Integration code tested against mocked APIs (no network, no keys)."""
import json
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.curves.alert import hotspot_ahead
from app.integrations import google_maps, hotspots, http, osm, weather
from app.main import create_app

from .helpers import ORIGIN, offset, road_with_curve


def encode_polyline(points):
    def enc(v):
        v = ~(v << 1) if v < 0 else v << 1
        out = ""
        while v >= 0x20:
            out += chr((0x20 | (v & 0x1F)) + 63)
            v >>= 5
        return out + chr(v + 63)

    res, plat, plon = "", 0, 0
    for lat, lon in points:
        ilat, ilon = round(lat * 1e5), round(lon * 1e5)
        res += enc(ilat - plat) + enc(ilon - plon)
        plat, plon = ilat, ilon
    return res


ROAD = road_with_curve(radius_m=40)


def overpass_payload():
    """Main road (north, then curve east) + a side street branching west at node 20."""
    elements = [{"type": "node", "id": i + 1, "lat": lat, "lon": lon} for i, (lat, lon) in enumerate(ROAD)]
    side = [offset(ORIGIN, -x, 100) for x in range(10, 200, 10)]
    elements += [{"type": "node", "id": 1000 + i, "lat": lat, "lon": lon} for i, (lat, lon) in enumerate(side)]
    elements.append({"type": "way", "id": 1, "nodes": list(range(1, len(ROAD) + 1)), "tags": {"highway": "primary"}})
    elements.append({"type": "way", "id": 2, "nodes": [21] + [1000 + i for i in range(len(side))], "tags": {"highway": "residential"}})
    return {"elements": elements}


def handler(request: httpx.Request) -> httpx.Response:
    host, path = request.url.host, request.url.path
    if host == "overpass-api.de":
        return httpx.Response(200, json=overpass_payload())
    if host == "api.open-meteo.com":
        return httpx.Response(200, json={"current": {"precipitation": 1.2, "rain": 1.2, "is_day": 0}, "hourly": {"precipitation": [0.8, 1.0, 0.4]}})
    if host == "routes.googleapis.com":
        body = json.loads(request.content)
        assert request.headers["X-Goog-FieldMask"] and body["polylineQuality"] == "HIGH_QUALITY"
        if body["travelMode"] == "DRIVE":
            assert body["routeModifiers"]["avoidHighways"] is True
        return httpx.Response(200, json={"routes": [{"polyline": {"encodedPolyline": encode_polyline(ROAD)}}]})
    if host == "roads.googleapis.com":
        pts = request.url.params["path"].split("|")
        snapped = [{"location": {"latitude": float(p.split(",")[0]) + 1e-5, "longitude": float(p.split(",")[1])}} for p in pts]
        return httpx.Response(200, json={"snappedPoints": snapped})
    if host == "places.googleapis.com":
        assert request.headers["X-Goog-FieldMask"].startswith("places.")
        body = json.loads(request.content)
        assert body["openNow"] is True and "circle" in body["locationBias"]
        return httpx.Response(200, json={"places": [{"id": "p1", "displayName": {"text": "JM Motorshop"}, "formattedAddress": "Sampaloc, Manila", "location": {"latitude": 14.6, "longitude": 120.99}, "nationalPhoneNumber": "0917 000 0000", "currentOpeningHours": {"openNow": True}}]})
    return httpx.Response(404)


@pytest.fixture(autouse=True)
def mock_http():
    http.set_transport(httpx.MockTransport(handler))
    yield
    http.set_transport(None)


@pytest.fixture
def keys(monkeypatch):
    fake = SimpleNamespace(google_maps_api_key="test-key", anthropic_api_key=None, llm_model="x", db_path=":memory:", route_travel_mode="TWO_WHEELER")
    for mod in ("app.main", "app.ride", "app.llm.assistant"):
        monkeypatch.setattr(f"{mod}.settings", fake)
    return fake


@pytest.mark.parametrize("mode", ["TWO_WHEELER", "DRIVE"])
def test_routes_api(mode):
    pts = google_maps.route(ORIGIN, ROAD[-1], "k", mode)
    assert len(pts) == len(ROAD)
    assert pts[0] == pytest.approx(ROAD[0], abs=1e-5)


def test_routes_api_error_is_reported():
    http.set_transport(httpx.MockTransport(lambda r: httpx.Response(403, text="API key not valid")))
    with pytest.raises(google_maps.MapsError, match="403"):
        google_maps.route(ORIGIN, ROAD[-1], "k")


def test_snap_and_places():
    assert len(ROAD) > 100
    assert len(google_maps.snap_to_roads(ROAD, "k")) == len(ROAD)  # sent in 2 batches of <=100
    shops = google_maps.repair_shops(ORIGIN, "k")
    assert shops[0]["name"] == "JM Motorshop" and shops[0]["open_now"] is True


@pytest.fixture(autouse=True)
def fresh_weather_cache():
    weather._cache.clear()
    yield
    weather._cache.clear()


def test_weather_wet_at_night():
    wx = weather.road_weather(ORIGIN)
    assert wx.wet and wx.is_day is False


def test_osm_road_ahead_follows_main_road_through_intersection():
    graph = osm.fetch_roads(ORIGIN)
    ahead = osm.road_ahead(graph, offset(ORIGIN, 2, 5), heading_deg=0, lookahead_m=600)
    # it should continue north through the side-street junction and round the curve (ends east of origin)
    assert ahead[-1][1] > ORIGIN[1] + 0.0004


def test_osm_respects_heading():
    graph = osm.fetch_roads(ORIGIN)
    backwards = osm.road_ahead(graph, offset(ORIGIN, 0, 200), heading_deg=180, lookahead_m=600)
    assert backwards[-1][0] < offset(ORIGIN, 0, 200)[0]  # went south


def test_oneway_blocks_wrong_direction():
    data = overpass_payload()
    data["elements"][-2]["tags"]["oneway"] = "yes"  # main road one-way northbound
    graph = osm.parse_overpass(data)
    ahead = osm.road_ahead(graph, offset(ORIGIN, 0, 200), heading_deg=180, lookahead_m=300)
    assert ahead[-1][0] > offset(ORIGIN, 0, 200)[0]  # can't go south, so path continues north


def test_hotspots_along_route(tmp_path):
    f = tmp_path / "h.geojson"
    spot = offset(ORIGIN, 5, 150)
    far = offset(ORIGIN, 500, 150)
    f.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [spot[1], spot[0]]}, "properties": {"name": "España-Lacson"}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [far[1], far[0]]}, "properties": {"name": "far away"}},
    ]}))
    from app.curves.geometry import find_curves

    pts, cum, _ = find_curves(ROAD)
    hits = hotspots.along_route(pts, cum, hotspots.load(str(f)))
    assert [h["name"] for h in hits] == ["España-Lacson"]
    assert hotspot_ahead(50, hits)["distance_m"] == 100
    assert hotspot_ahead(0, hits)["distance_m"] == 150  # warn range is inclusive
    assert hotspot_ahead(160, hits) is None  # already passed


def test_ride_start_with_destination_uses_google(keys):
    c = TestClient(create_app(db_path=":memory:"))
    r = c.post("/ride/start", json={"position": ORIGIN, "destination": ROAD[-1]}).json()
    assert r["source"] == "google_routes"
    assert len(r["curves"]) == 1
    assert r["conditions"] == {"wet": True, "night": True}


def test_ride_start_without_destination_uses_osm():
    c = TestClient(create_app(db_path=":memory:"))
    r = c.post("/ride/start", json={"position": offset(ORIGIN, 2, 5), "heading_deg": 0}).json()
    assert r["source"] == "osm_road_ahead"
    assert len(r["curves"]) == 1
    assert r["curves"][0]["safe_kmh_wet"] < r["curves"][0]["safe_kmh_dry"]


def test_ride_start_survives_weather_outage(monkeypatch):
    def broken(_):
        raise httpx.ConnectError("down")

    monkeypatch.setattr("app.ride.weather.road_weather", broken)
    r = TestClient(create_app(db_path=":memory:")).post("/ride/start", json={"position": offset(ORIGIN, 2, 5), "heading_deg": 0}).json()
    assert r["conditions"]["wet"] is False
    assert any("Weather unavailable" in w for w in r["warnings"])


def test_shop_endpoints_need_key():
    c = TestClient(create_app(db_path=":memory:"))
    tok = c.post("/bikes", json={"make": "Honda", "model": "Click"}).json()["token"]
    assert c.get("/shops/nearby", params={"lat": 14.6, "lon": 121.0}, headers={"X-Bike-Token": tok}).status_code == 503


def test_shops_endpoint(keys):
    c = TestClient(create_app(db_path=":memory:"))
    tok = c.post("/bikes", json={"make": "Honda", "model": "Click"}).json()["token"]
    r = c.get("/shops/nearby", params={"lat": 14.6, "lon": 121.0}, headers={"X-Bike-Token": tok})
    assert r.status_code == 200 and r.json()[0]["phone"]


def test_weather_endpoint_503_when_upstream_down():
    http.set_transport(httpx.MockTransport(lambda r: httpx.Response(502)))
    r = TestClient(create_app(db_path=":memory:")).get("/weather", params={"lat": 14.6, "lon": 121.0})
    assert r.status_code == 503


def test_overpass_falls_back_to_mirror():
    seen = []

    def h(req):
        seen.append(req.url.host)
        if req.url.host == "overpass-api.de":
            return httpx.Response(502, text="# 502 Gateway Timeout")
        return httpx.Response(200, json=overpass_payload())

    http.set_transport(httpx.MockTransport(h))
    graph = osm.fetch_roads(ORIGIN)
    assert seen == ["overpass-api.de", "overpass.kumi.systems"] and graph.adj


def test_overpass_all_down_reports_each_server():
    http.set_transport(httpx.MockTransport(lambda r: httpx.Response(502)))
    with pytest.raises(RuntimeError, match="overpass-api.de.*overpass.kumi.systems"):
        osm.fetch_roads(ORIGIN)


def test_tile_cache_serves_rides_without_network(tmp_path, monkeypatch):
    monkeypatch.setattr(osm, "TILE_DIR", tmp_path)
    # prewarm: every tile around the start gets the demo road
    for t in osm.tiles_for_circle(ORIGIN, 800):
        osm.save_tile(t, overpass_payload()["elements"])
    calls = []
    http.set_transport(httpx.MockTransport(lambda r: calls.append(r.url.host) or httpx.Response(502)))
    graph = osm.fetch_roads(offset(ORIGIN, 2, 5), radius_m=800)
    ahead = osm.road_ahead(graph, offset(ORIGIN, 2, 5), heading_deg=0, lookahead_m=600)
    assert calls == [] and len(ahead) > 10


def test_partial_cache_falls_back_to_live(tmp_path, monkeypatch):
    monkeypatch.setattr(osm, "TILE_DIR", tmp_path)
    osm.save_tile(osm.tile_of(*ORIGIN), overpass_payload()["elements"])  # only one tile
    graph = osm.fetch_roads(ORIGIN, radius_m=3000)  # needs more tiles -> live (mocked OK)
    assert graph.adj


def test_prewarm_script(tmp_path, monkeypatch):
    from tools import prewarm_osm
    monkeypatch.setattr(osm, "TILE_DIR", tmp_path)
    assert prewarm_osm.main(["--around", f"{ORIGIN[0]},{ORIGIN[1]}", "--km", "0.5", "--pause", "0"]) == 0
    assert len(list(tmp_path.glob("*.json.gz"))) >= 1
    assert prewarm_osm.main(["--around", f"{ORIGIN[0]},{ORIGIN[1]}", "--km", "0.5", "--pause", "0"]) == 0  # all cached now


def test_split_into_tiles_matches_cache_lookup(tmp_path, monkeypatch):
    monkeypatch.setattr(osm, "TILE_DIR", tmp_path)
    pieces = osm.split_into_tiles(overpass_payload()["elements"])
    for t in osm.tiles_for_circle(ORIGIN, 600):
        osm.save_tile(t, pieces.get(t, []))
    http.set_transport(httpx.MockTransport(lambda r: httpx.Response(502)))  # no network allowed
    ahead = osm.road_ahead(osm.fetch_roads(offset(ORIGIN, 2, 5), 600), offset(ORIGIN, 2, 5), 0, 600)
    assert ahead[-1][1] > ORIGIN[1] + 0.0004  # follows the road round the curve, from tiles only


def test_tiles_from_osm_extract(tmp_path, monkeypatch):
    pytest.importorskip("osmium")
    from tools import tiles_from_pbf
    # write the demo road as a tiny .osm file (same format family as Geofabrik's .osm.pbf)
    els = overpass_payload()["elements"]
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<osm version="0.6">']
    for e in els:
        if e["type"] == "node":
            lines.append(f'<node id="{e["id"]}" version="1" lat="{e["lat"]}" lon="{e["lon"]}"/>')
    for e in els:
        if e["type"] == "way":
            lines.append(f'<way id="{e["id"]}" version="1">' + "".join(f'<nd ref="{n}"/>' for n in e["nodes"])
                         + f'<tag k="highway" v="{e["tags"]["highway"]}"/></way>')
    lines.append('<way id="99" version="1"><nd ref="1"/><nd ref="2"/><tag k="waterway" v="river"/></way>')  # not a road
    lines.append("</osm>")
    f = tmp_path / "area.osm"
    f.write_text("\n".join(lines))
    monkeypatch.setattr(osm, "TILE_DIR", tmp_path / "tiles")
    s, w = ORIGIN[0] - 0.01, ORIGIN[1] - 0.01
    assert tiles_from_pbf.main(["--pbf", str(f), "--bbox", f"{s},{w},{ORIGIN[0] + 0.01},{ORIGIN[1] + 0.01}"]) == 0
    http.set_transport(httpx.MockTransport(lambda r: httpx.Response(502)))  # offline
    graph = osm.fetch_roads(offset(ORIGIN, 2, 5), 600)
    ahead = osm.road_ahead(graph, offset(ORIGIN, 2, 5), 0, 600)
    assert ahead[-1][1] > ORIGIN[1] + 0.0004


def test_weather_falls_back_to_met_norway_then_cache():
    metno = {"properties": {"timeseries": [{"data": {"instant": {"details": {}}, "next_1_hours": {"details": {"precipitation_amount": 1.4}}}}]}}
    calls = []

    def h(req):
        calls.append(req.url.host)
        if req.url.host == "api.open-meteo.com":
            return httpx.Response(429, text="Too Many Requests")
        return httpx.Response(200, json=metno)

    http.set_transport(httpx.MockTransport(h))
    wx = weather.road_weather(ORIGIN)
    assert wx.source == "met.no" and wx.wet and calls == ["api.open-meteo.com", "api.met.no"]
    assert weather.road_weather(ORIGIN) is wx and len(calls) == 2  # cached: no new calls
    # everything down, cache expired but recent: serve the last reading, marked cached
    weather._cache[(round(ORIGIN[0], 1), round(ORIGIN[1], 1))] = (time.time() - 1800, wx)
    http.set_transport(httpx.MockTransport(lambda r: httpx.Response(503)))
    stale = weather.road_weather(ORIGIN)
    assert stale.wet and stale.source.endswith("(cached)")


def test_edge_of_cached_area_uses_partial_tiles(tmp_path, monkeypatch):
    monkeypatch.setattr(osm, "TILE_DIR", tmp_path)
    pieces = osm.split_into_tiles(overpass_payload()["elements"])
    osm.save_tile(osm.tile_of(*ORIGIN), pieces[osm.tile_of(*ORIGIN)])  # only the rider's tile
    http.set_transport(httpx.MockTransport(lambda r: httpx.Response(502)))  # live servers down
    graph = osm.fetch_roads(ORIGIN, radius_m=3000)
    assert graph.adj
