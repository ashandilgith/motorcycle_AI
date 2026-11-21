from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.llm import assistant
from app.main import create_app

from .helpers import ORIGIN, engine_wav, offset, road_with_curve


def client():
    return TestClient(create_app(db_path=":memory:"))


def test_health():
    assert client().get("/health").json()["ok"] is True


def test_bike_service_and_maintenance():
    c = client()
    bike = c.post("/bikes", json={"make": "Honda", "model": "Click 125", "year": 2023, "odometer_km": 5200, "usage": "gig"}).json()
    c.post(f"/bikes/{bike['id']}/service", json={"item": "engine_oil", "odometer_km": 3800, "cost_php": 350})
    due = c.get(f"/bikes/{bike['id']}/maintenance").json()
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
    bike = c.post("/bikes", json={"make": "Yamaha", "model": "NMAX"}).json()
    url = f"/bikes/{bike['id']}/sound"
    for s in range(3):
        r = c.post(url, params={"baseline": True}, files={"file": ("a.wav", engine_wav(seed=s), "audio/wav")})
        assert r.status_code == 200
    bad = c.post(url, files={"file": ("b.wav", engine_wav(seed=9, knock=0.8), "audio/wav")}).json()
    assert bad["is_anomaly"] is True
    assert c.post(f"/clips/{bad['clip_id']}/label", json={"label": "valve_tick"}).json()["ok"]


def test_tyre_check_without_key_is_503(monkeypatch):
    monkeypatch.setattr("app.llm.vision.settings", SimpleNamespace(anthropic_api_key=None))
    r = client().post("/tyre/check", files={"file": ("t.jpg", b"\xff\xd8\xff\xe0", "image/jpeg")})
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
