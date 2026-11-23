"""Rain -> the `wet` flag for curve safe speeds.

Uses Open-Meteo (free, no key). Its 15-minute data only covers Central Europe and
North America; for Manila it interpolates hourly data, so this is coarse. Upgrade
path: a radar nowcast (e.g. PAGASA or a paid nowcast API) for "rain in the next 15 min".

Reliability: free hosts share outgoing IP addresses, so Open-Meteo can answer 429 (rate
limited) because of other apps. We cache each ~10 km area for 10 minutes, fall back to
MET Norway (api.met.no, free, needs an identifying User-Agent), and as a last resort serve
the last good reading if it's under 2 hours old.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from ..curves.geometry import LatLon
from .http import client

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
METNO_URL = "https://api.met.no/weatherapi/locationforecast/2.0/compact"
CACHE_TTL_S = 600
STALE_OK_S = 2 * 3600
WET_NOW_MM = 0.1         # any measurable rain now
WET_RECENT_MM = 0.5      # roads stay slick after rain (oil film on first rain is worst)


@dataclass
class RoadWeather:
    wet: bool
    rain_now_mm: float
    rain_last_hour_mm: float
    rain_next_hour_mm: float
    is_day: bool | None
    source: str = "open-meteo"


_cache: dict[tuple[float, float], tuple[float, "RoadWeather"]] = {}


def road_weather(position: LatLon) -> RoadWeather:
    """Cached, with a fallback provider and a stale-but-recent last resort."""
    key = (round(position[0], 1), round(position[1], 1))  # ~10 km cells: rain doesn't need finer
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL_S:
        return hit[1]
    errors = []
    for fetch in (_open_meteo, _met_norway):
        try:
            wx = fetch(position)
            _cache[key] = (time.time(), wx)
            return wx
        except Exception as exc:
            errors.append(f"{fetch.__name__.strip('_')}: {exc}")
    if hit and time.time() - hit[0] < STALE_OK_S:
        return replace(hit[1], source=hit[1].source + " (cached)")
    raise RuntimeError("; ".join(errors))


def _is_day_by_clock(lon: float) -> bool:
    """Rough day/night from local solar time (good to about half an hour in the tropics)."""
    now = datetime.now(timezone.utc)
    solar_hour = (now.hour + now.minute / 60 + lon / 15) % 24
    return 6 <= solar_hour < 18


def _met_norway(position: LatLon) -> RoadWeather:
    params = {"lat": round(position[0], 4), "lon": round(position[1], 4)}
    with client(timeout=10) as c:
        data = c.get(METNO_URL, params=params).raise_for_status().json()
    series = data["properties"]["timeseries"]
    nxt = float(series[0]["data"].get("next_1_hours", {}).get("details", {}).get("precipitation_amount", 0.0) or 0.0)
    return RoadWeather(
        wet=nxt >= WET_NOW_MM,  # MET Norway gives the next hour, not "now": treat rain this hour as wet
        rain_now_mm=nxt,
        rain_last_hour_mm=0.0,
        rain_next_hour_mm=nxt,
        is_day=_is_day_by_clock(position[1]),
        source="met.no",
    )


def _open_meteo(position: LatLon) -> RoadWeather:
    params = {
        "latitude": position[0],
        "longitude": position[1],
        "current": "precipitation,rain,is_day",
        "hourly": "precipitation",
        "past_hours": 1,
        "forecast_hours": 2,
        "timezone": "auto",
    }
    with client(timeout=10) as c:
        data = c.get(FORECAST_URL, params=params).raise_for_status().json()
    cur = data.get("current", {})
    hourly = data.get("hourly", {}).get("precipitation", []) or [0.0]
    now = float(cur.get("precipitation") or 0.0)
    last = float(hourly[0] or 0.0)
    nxt = float(hourly[-1] or 0.0)
    is_day = cur.get("is_day")
    return RoadWeather(
        wet=now >= WET_NOW_MM or last >= WET_RECENT_MM,
        rain_now_mm=now,
        rain_last_hour_mm=last,
        rain_next_hour_mm=nxt,
        is_day=None if is_day is None else bool(is_day),
    )
