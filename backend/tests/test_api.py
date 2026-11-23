from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.llm import assistant
from app.main import create_app

from .helpers import ORIGIN, engine_wav, offset, road_with_curve


def client():
    return TestClient(create_app(db_path=":memory:"))


def register(c, **kw):
    bike = c.post("/bikes", json={"make": "Honda", "model": "Click 125", **kw}).json()
    return bike, {"X-Bike-Token": bike["token"]}


def test_health():
    assert client().get("/health").json()["ok"] is True


def test_bike_service_and_maintenance():
    c = client()
    bike, h = register(c, year=2023, odometer_km=5200, usage="gig")
    c.post(f"/bikes/{bike['id']}/service", json={"item": "engine_oil", "odometer_km": 3800, "cost_php": 350}, headers=h)
    due = c.get(f"/bikes/{bike['id']}/maintenance", headers=h).json()
    oil = next(r for r in due if r["item"] == "engine_oil")
    assert oil["km_since_last"] == 1400
    assert oil["status"] in {"due_soon", "ok", "overdue"}
    assert c.get("/bikes/nope").status_code == 404


def test_curves_and_alert_endpoints():
    c = client()
    pts = road_with_curve(radius_m=40)
    curves = c.post("/routes/curves/from-points", json={"points": pts}).json()
    assert curves["count"] == 1
    r = c.post("/ride/alert", json={"points": pts, "position": offset(ORIGIN, 0, 260), "speed_kmh": 70}).json()
    assert r["level"] == "urgent"
    assert r["message_tl"] and r["action_distance_m"] < r["distance_to_curve_m"]


def test_sound_baseline_then_check():
    c = client()
    bike, h = register(c, model="NMAX")
    url = f"/bikes/{bike['id']}/sound"
    for s in range(3):
        r = c.post(url, params={"baseline": True}, files={"file": ("a.wav", engine_wav(seed=s), "audio/wav")}, headers=h)
        assert r.status_code == 200
    bad = c.post(url, files={"file": ("b.wav", engine_wav(seed=9, knock=0.8), "audio/wav")}, headers=h).json()
    assert bad["is_anomaly"] is True
    assert c.post(f"/clips/{bad['clip_id']}/label", json={"label": "valve_tick"}, headers=h).json()["ok"]


def test_tyre_check_without_key_is_503(monkeypatch):
    monkeypatch.setattr("app.llm.vision.settings", SimpleNamespace(anthropic_api_key=None))
    c = client()
    _, h = register(c)
    r = c.post("/tyre/check", files={"file": ("t.jpg", b"\xff\xd8\xff\xe0", "image/jpeg")}, headers=h)
    assert r.status_code == 503


class FakeLLM:
    """Plays one tool call, then a final answer, like the real Messages API."""

    def __init__(self):
        self.calls = 0
        self.messages = self

    def create(self, **kw):
        self.calls += 1
        if self.calls == 1:
            return SimpleNamespace(
                stop_reason="tool_use",
                content=[SimpleNamespace(type="tool_use", id="t1", name="get_bike_profile", input={})],
            )
        tool_result = kw["messages"][-1]["content"][0]["content"]
        assert "Click 125" in tool_result
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="Check muna ang battery, paps.")])


def test_assistant_tool_loop():
    app = create_app(db_path=":memory:")
    store = app.state.store
    bike = store.add_bike("Honda", "Click 125", 2023, 5200, "commuter")
    out = assistant.chat(store, bike["id"], [{"role": "user", "content": "Ayaw mag start"}], client=FakeLLM())
    assert out["tools_used"] == ["get_bike_profile"]
    assert "battery" in out["reply"]


def test_input_validation():
    c = client()
    assert c.post("/ride/alert", json={"points": [[0, 0], [0, 1], [0, 2]], "position": [999, 0], "speed_kmh": 10}).status_code == 422
    bike = c.post("/bikes", json={"make": "Honda", "model": "Click"}).json()
    assert c.post(f"/bikes/{bike['id']}/service", json={"item": "engine_oil", "odometer_km": -5}).status_code == 422
    assert c.post("/bikes", json={"make": "Honda", "model": "Click", "odometer_km": -1}).status_code == 422
    assert c.post("/clips/nope/label", json={"label": "x"}).status_code == 404


def test_bike_data_needs_its_own_key():
    c = client()
    a, ha = register(c)
    b, hb = register(c)
    assert "token" in a and "token_hash" not in c.get(f"/bikes/{a['id']}", headers=ha).json()
    assert c.get(f"/bikes/{a['id']}").status_code == 404                      # no key
    assert c.get(f"/bikes/{a['id']}", headers=hb).status_code == 404          # another bike's key
    assert c.get(f"/bikes/{a['id']}", headers={"X-Bike-Token": "guess"}).status_code == 404
    assert c.post(f"/bikes/{a['id']}/service", json={"item": "x", "odometer_km": 1}, headers=hb).status_code == 404
    assert c.get(f"/bikes/{a['id']}/service", headers=ha).status_code == 200


def test_delete_bike_erases_everything():
    c = client()
    bike, h = register(c)
    c.post(f"/bikes/{bike['id']}/service", json={"item": "engine_oil", "odometer_km": 100}, headers=h)
    c.post(f"/bikes/{bike['id']}/sound", params={"baseline": True}, files={"file": ("a.wav", engine_wav(), "audio/wav")}, headers=h)
    assert c.delete(f"/bikes/{bike['id']}", headers=h).status_code == 204
    assert c.get(f"/bikes/{bike['id']}", headers=h).status_code == 404
    st = c.app.state.store
    assert st.service_log(bike["id"]) == [] and st.recent_clips(bike["id"]) == []


def test_ai_endpoints_need_a_registered_bike():
    c = client()
    assert c.post("/tyre/check", files={"file": ("t.jpg", b"\xff\xd8\xff\xe0", "image/jpeg")}).status_code == 401
    assert c.get("/shops/nearby", params={"lat": 14.6, "lon": 121.0}).status_code == 401


def test_upload_size_limits():
    c = client()
    bike, h = register(c)
    big = b"RIFF" + b"0" * (5 * 1024 * 1024 + 10)
    r = c.post(f"/bikes/{bike['id']}/sound", files={"file": ("big.wav", big, "audio/wav")}, headers=h)
    assert r.status_code == 413


def test_rate_limit_on_ride_start(monkeypatch):
    import app.main as m
    monkeypatch.setitem(m.RATE_LIMITS, "ride", 2)
    monkeypatch.setattr(m, "start_ride", lambda *a, **k: {"ok": True})
    c = client()
    codes = [c.post("/ride/start", json={"position": [14.6, 121.0]}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_client_errors_are_logged(caplog):
    c = client()
    with caplog.at_level("WARNING"):
        assert c.post("/client-errors", json={"message": "TypeError: x is undefined", "where": "app.js:12"}).status_code == 204
    assert "TypeError" in caplog.text


def test_unlogged_items_are_unknown_not_overdue():
    c = client()
    bike, h = register(c, odometer_km=20000)
    due = {r["item"]: r["status"] for r in c.get(f"/bikes/{bike['id']}/maintenance", headers=h).json()}
    assert due["brake_pads_check"] == "unknown"
    c.post(f"/bikes/{bike['id']}/service", json={"item": "brake_pads_check", "odometer_km": 15000}, headers=h)
    due = {r["item"]: r["status"] for r in c.get(f"/bikes/{bike['id']}/maintenance", headers=h).json()}
    assert due["brake_pads_check"] == "overdue"


def test_nmax_uses_manual_intervals_others_are_estimates():
    c = client()
    nmax, h = register(c, model="NMAX 155", odometer_km=1000)
    rows = {r["item"]: r for r in c.get(f"/bikes/{nmax['id']}/maintenance", headers=h).json()}
    assert rows["coolant"]["source"] == "manual" and rows["coolant"]["interval_km"] == round(12000 * 0.9)
    assert rows["v_belt"]["source"] == "manual"
    assert rows["spark_plug"]["source"] == "estimate"
    click, h2 = register(c, model="Click 125")
    rows = {r["item"]: r for r in c.get(f"/bikes/{click['id']}/maintenance", headers=h2).json()}
    assert "v_belt" not in rows and all(r["source"] == "estimate" for r in rows.values())
