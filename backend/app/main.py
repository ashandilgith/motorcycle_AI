"""FastAPI backend for the motorcycle_AI MVP.

Run:  uvicorn app.main:app --reload   (from backend/)
Docs: http://localhost:8000/docs
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import settings
from .curves import alert as alert_mod
from .curves.geometry import distance_along, find_curves
from .demo import ORIGIN, offset, road_with_curve
from .integrations import google_maps, hotspots, weather
from .ride import start_ride
from .llm import assistant, vision
from .maintenance import next_due
from .sound import anomaly
from .sound.features import embed, read_wav
from .storage import Store

LatLonIn = tuple[Annotated[float, Field(ge=-90, le=90)], Annotated[float, Field(ge=-180, le=180)]]
WEB_DIR = Path(__file__).resolve().parents[2] / "web"
VERSION = "0.2.0"

# The bike's access key, sent by the phone on every bike-scoped call (module level so FastAPI can resolve it).
Token = Annotated[str | None, Header(alias="X-Bike-Token")]

MAX_SOUND_BYTES = 5 * 1024 * 1024   # ~10 s of 16-bit WAV is under 1 MB
MAX_IMAGE_BYTES = 8 * 1024 * 1024
# Requests per minute per client IP, for endpoints that cost money or hit shared services.
RATE_LIMITS = {"ride": 30, "weather": 30, "shops": 10, "ai": 10, "client_error": 20}

log = logging.getLogger("motorcycle_ai")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


class RateLimiter:
    """Small in-memory sliding-window limiter (one server process). Swap for Redis if you run several."""

    def __init__(self):
        self.hits: dict[tuple[str, str], deque] = defaultdict(deque)

    def check(self, bucket: str, key: str, per_minute: int) -> None:
        now = time.monotonic()
        q = self.hits[(bucket, key)]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= per_minute:
            raise HTTPException(429, "too many requests, slow down", headers={"Retry-After": "60"})
        q.append(now)


# ---------- request models ----------
class BikeIn(BaseModel):
    make: str = Field(examples=["Honda"])
    model: str = Field(examples=["Click 125"])
    year: int | None = None
    odometer_km: float = Field(0, ge=0)
    usage: Literal["commuter", "gig", "diy"] = "commuter"


class ServiceIn(BaseModel):
    item: str = Field(examples=["engine_oil"])
    odometer_km: float = Field(ge=0)
    cost_php: float | None = None
    notes: str | None = None


class RouteIn(BaseModel):
    origin: LatLonIn
    destination: LatLonIn


class PointsIn(BaseModel):
    points: list[LatLonIn] = Field(min_length=3)


class AlertIn(BaseModel):
    points: list[LatLonIn] = Field(min_length=3, description="Route polyline")
    position: LatLonIn
    speed_kmh: float = Field(ge=0)
    wet: bool = False
    night: bool = False


class RideStartIn(BaseModel):
    position: LatLonIn
    destination: LatLonIn | None = None
    heading_deg: float | None = Field(None, ge=0, lt=360, description="GPS course over ground")
    lookahead_m: float = Field(1500, ge=200, le=5000)
    roads: list[dict] | None = Field(
        None, max_length=80_000,
        description="OpenStreetMap elements around the rider (Overpass JSON), sent by the phone when the server has no roads here",
    )


class ChatIn(BaseModel):
    location: LatLonIn | None = Field(None, description="Rider's position, enables the shop-finder tool")
    messages: list[dict] = Field(examples=[[{"role": "user", "content": "Mga paps, ayaw mag start, may pumipitik lang"}]])


class LabelIn(BaseModel):
    label: str = Field(max_length=80, examples=["cvt_belt_worn"])


class ClientErrorIn(BaseModel):
    message: str = Field(max_length=2000)
    where: str | None = Field(None, max_length=300)
    version: str | None = Field(None, max_length=40)


# ---------- app ----------
def create_app(db_path: str | None = None) -> FastAPI:
    app = FastAPI(title="motorcycle_AI", version=VERSION)
    app.state.store = Store(db_path or settings.db_path)
    limiter = RateLimiter()

    @app.exception_handler(Exception)
    async def unhandled(req: Request, exc: Exception):
        log.exception("unhandled error on %s %s", req.method, req.url.path)
        return JSONResponse({"detail": "internal error"}, status_code=500)

    @app.middleware("http")
    async def body_cap(req: Request, call_next):
        # JSON bodies (ride start with phone roads is the biggest) stay under 4 MB; uploads have their own caps
        if req.headers.get("content-type", "").startswith("application/json"):
            try:
                size = int(req.headers.get("content-length") or 0)
            except ValueError:
                size = 0
            if size > 4 * 1024 * 1024:
                return JSONResponse({"detail": "request too large"}, status_code=413)
        return await call_next(req)

    def store(req: Request) -> Store:
        return req.app.state.store

    def client_ip(req: Request) -> str:
        fwd = req.headers.get("x-forwarded-for")  # behind a hosting proxy
        return fwd.split(",")[0].strip() if fwd else (req.client.host if req.client else "?")

    def limit(req: Request, bucket: str) -> None:
        limiter.check(bucket, client_ip(req), RATE_LIMITS[bucket])

    def bike_or_404(req: Request, bike_id: str, token: str | None) -> dict:
        """The bike, if the caller holds its access key (X-Bike-Token). 404 either way, so ids can't be probed."""
        bike = store(req).get_bike(bike_id)
        if not bike or not store(req).check_token(bike_id, token):
            raise HTTPException(404, "bike not found")
        return bike

    def require_any_bike(req: Request, token: str | None) -> None:
        """AI features cost money per call: only for riders who registered a bike."""
        if not store(req).any_valid_token(token):
            raise HTTPException(401, "register a bike first (X-Bike-Token)")

    async def read_capped(file: UploadFile, cap: int) -> bytes:
        data = await file.read(cap + 1)
        if len(data) > cap:
            raise HTTPException(413, f"file too large (max {cap // (1024 * 1024)} MB)")
        return data

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/app/")

    @app.get("/demo/ride")
    def demo_ride():
        """A synthetic road (300 m straight into a 40 m-radius curve) for trying alerts without riding."""
        pts, cum, curves = find_curves(road_with_curve(radius_m=40))
        exit_spot = offset(ORIGIN, 40 + 120, 340)  # on the exit straight, 120 m after the curve
        hs = hotspots.along_route(pts, cum, [{"name": "Demo intersection", "lat": exit_spot[0], "lon": exit_spot[1]}])
        return {
            "source": "demo",
            "points": pts,
            "length_m": round(cum[-1], 1),
            "curves": [asdict(c) for c in curves],
            "hotspots": hs,
            "conditions": {"wet": False, "night": False},
            "warnings": [],
        }

    @app.get("/health")
    def health():
        return {
            "ok": True,
            "version": VERSION,
            "llm_configured": bool(settings.anthropic_api_key),
            "maps_configured": bool(settings.google_maps_api_key),
        }

    @app.post("/client-errors", status_code=204)
    def client_error(body: ClientErrorIn, req: Request):
        """The phone reports its own JavaScript errors here, so they show up in the server log."""
        limit(req, "client_error")
        log.warning("client error v=%s where=%s: %s", body.version, body.where, body.message)

    # Bikes + maintenance. Every /bikes/{id} call needs the X-Bike-Token returned at registration.

    @app.post("/bikes")
    def add_bike(body: BikeIn, req: Request):
        """Register a bike. The response's `token` is the bike's access key: shown once, keep it on the phone."""
        limit(req, "ai")
        return store(req).add_bike(body.make, body.model, body.year, body.odometer_km, body.usage)

    @app.get("/bikes/{bike_id}")
    def get_bike(bike_id: str, req: Request, token: Token = None):
        return bike_or_404(req, bike_id, token)

    @app.delete("/bikes/{bike_id}", status_code=204)
    def delete_bike(bike_id: str, req: Request, token: Token = None):
        """Erase the bike, its service log and sound data."""
        bike_or_404(req, bike_id, token)
        store(req).delete_bike(bike_id)

    @app.get("/bikes/{bike_id}/service")
    def service_log(bike_id: str, req: Request, token: Token = None):
        bike_or_404(req, bike_id, token)
        return store(req).service_log(bike_id)

    @app.post("/bikes/{bike_id}/service")
    def add_service(bike_id: str, body: ServiceIn, req: Request, token: Token = None):
        bike_or_404(req, bike_id, token)
        return store(req).add_service(bike_id, body.item, body.odometer_km, body.cost_php, body.notes)

    @app.get("/bikes/{bike_id}/maintenance")
    def maintenance(bike_id: str, req: Request, token: Token = None):
        bike = bike_or_404(req, bike_id, token)
        return next_due(bike["odometer_km"], bike["usage"], store(req).service_log(bike_id), bike["model"])

    # Curves
    def _curves_payload(points):
        _, _, curves = find_curves(points)
        return {"count": len(curves), "curves": [asdict(c) for c in curves]}

    def maps_key() -> str:
        if not settings.google_maps_api_key:
            raise HTTPException(503, "GOOGLE_MAPS_API_KEY not set")
        return settings.google_maps_api_key

    @app.post("/routes/curves")
    def route_curves(body: RouteIn):
        """Fetch a route from the Google Routes API and return its curves (sent to the phone once per ride)."""
        try:
            points = google_maps.route(body.origin, body.destination, maps_key(), settings.route_travel_mode)
        except google_maps.MapsError as exc:
            raise HTTPException(502, str(exc))
        return {"points": points, **_curves_payload(points)}

    @app.post("/ride/start")
    def ride_start(body: RideStartIn, req: Request):
        """Everything the phone needs at ride start: route/road ahead, curves, weather, hotspots."""
        limit(req, "ride")
        return start_ride(body.position, body.destination, body.heading_deg, body.lookahead_m, body.roads)

    @app.post("/roads/snap")
    def roads_snap(body: PointsIn, req: Request, token: Token = None):
        """Snap a recorded GPS trace to roads (Google Roads API)."""
        require_any_bike(req, token)
        limit(req, "shops")
        return {"points": google_maps.snap_to_roads(body.points, maps_key())}

    @app.get("/shops/nearby")
    def shops_nearby(req: Request, lat: float, lon: float, open_now: bool = True, radius_m: float = 3000, token: Token = None):
        require_any_bike(req, token)
        limit(req, "shops")
        return google_maps.repair_shops((lat, lon), maps_key(), radius_m=radius_m, open_now=open_now)

    @app.get("/weather")
    def road_weather(req: Request, lat: float, lon: float):
        limit(req, "weather")
        try:
            return asdict(weather.road_weather((lat, lon)))
        except Exception as exc:  # upstream down: tell the phone, which keeps its last value
            raise HTTPException(503, f"weather unavailable: {exc}")

    @app.post("/routes/curves/from-points")
    def curves_from_points(body: PointsIn):
        return _curves_payload(body.points)

    @app.post("/ride/alert")
    def ride_alert(body: AlertIn):
        """Server-side reference of the on-phone loop; handy for testing and simulation."""
        pts, cum, curves = find_curves(body.points)
        pos_m, off_m = distance_along(pts, cum, body.position)
        result = alert_mod.evaluate(pos_m, body.speed_kmh, curves, alert_mod.RideConditions(body.wet, body.night))
        out = asdict(result)
        out["level"] = result.level.value
        out["position_m"] = pos_m
        out["off_route_m"] = off_m
        return out

    # Sound
    @app.post("/bikes/{bike_id}/sound")
    async def sound_check(bike_id: str, req: Request, file: UploadFile = File(...), baseline: bool = False, token: Token = None):
        """Upload a 16-bit WAV (10 s idle + 3 revs). baseline=true while the bike is known healthy."""
        bike_or_404(req, bike_id, token)
        data = await read_capped(file, MAX_SOUND_BYTES)
        try:
            audio, rate = read_wav(data)
        except Exception as exc:
            raise HTTPException(400, f"could not read WAV: {exc}")
        emb = embed(audio, rate)
        st = store(req)
        result = anomaly.score(emb, st.baseline(bike_id))
        clip_id = st.add_clip(bike_id, emb, is_baseline=baseline, score=None if baseline else result.score)
        if baseline:
            return {"clip_id": clip_id, "baseline_clips": len(st.baseline(bike_id)), "note": "Saved as healthy baseline."}
        return {"clip_id": clip_id, **asdict(result)}

    @app.post("/clips/{clip_id}/label")
    def label_clip(clip_id: str, body: LabelIn, req: Request, token: Token = None):
        """Mechanic-confirmed finding -> training label for the future fault classifier."""
        bike_id = store(req).clip_bike(clip_id)
        if not bike_id or not store(req).check_token(bike_id, token):
            raise HTTPException(404, "clip not found")
        if not store(req).label_clip(clip_id, body.label):
            raise HTTPException(404, "clip not found")
        return {"ok": True}

    # Tyre (VLM)
    @app.post("/tyre/check")
    async def tyre_check(req: Request, file: UploadFile = File(...), token: Token = None):
        require_any_bike(req, token)
        limit(req, "ai")
        try:
            return vision.check_tyre(await read_capped(file, MAX_IMAGE_BYTES))
        except RuntimeError as exc:
            raise HTTPException(503, str(exc))
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    # Assistant
    @app.post("/bikes/{bike_id}/chat")
    def chat(bike_id: str, body: ChatIn, req: Request, token: Token = None):
        bike_or_404(req, bike_id, token)
        limit(req, "ai")
        try:
            return assistant.chat(store(req), bike_id, body.messages, location=body.location)
        except RuntimeError as exc:
            raise HTTPException(503, str(exc))

    if WEB_DIR.exists():  # the phone app (PWA)
        app.mount("/app", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app


app = create_app()
